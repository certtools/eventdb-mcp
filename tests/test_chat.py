"""Tests for the LLM tool-calling loop, with a scripted fake LLM."""

import asyncio
import json
from types import SimpleNamespace

from openai.types.chat import ChatCompletion

from eventdb_mcp import chat, server


def completion(content=None, tool_calls=None) -> ChatCompletion:
    """Build a real OpenAI ChatCompletion object."""
    message = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = [
            {"id": f"call_{i}", "type": "function", "function": {"name": n, "arguments": a}}
            for i, (n, a) in enumerate(tool_calls)
        ]
    return ChatCompletion.model_validate(
        {
            "id": "x",
            "object": "chat.completion",
            "created": 0,
            "model": "fake",
            "choices": [{"index": 0, "finish_reason": "stop", "message": message}],
        }
    )


class FakeLLM:
    """Returns the scripted completions in order and records every request."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.requests.append(json.loads(json.dumps(kwargs, default=str)))
        return self.responses.pop(0)


def run_ask(settings, llm, question="Which ASN has the most events?"):
    calls = []
    answer = asyncio.run(
        chat.ask(
            question,
            settings,
            server.create_server(settings),
            llm=llm,
            on_tool_call=lambda n, a: calls.append((n, a)),
        )
    )
    return answer, calls


def test_ask_runs_tool_and_returns_answer(settings):
    llm = FakeLLM(
        completion(tool_calls=[("event_stats", '{"group_by": "source.asn", "limit": 1}')]),
        completion("AS64496 has the most events (3)."),
    )
    answer, calls = run_ask(settings, llm)
    assert answer == "AS64496 has the most events (3)."
    assert calls == [("event_stats", '{"group_by": "source.asn", "limit": 1}')]

    first, second = llm.requests[0], llm.requests[1]
    assert first["model"] == settings.llm_model
    assert {t["function"]["name"] for t in first["tools"]} >= {"run_sql", "search_events"}
    assert "IntelMQ" in first["messages"][0]["content"]
    tool_msg = second["messages"][-1]
    assert tool_msg["role"] == "tool" and tool_msg["tool_call_id"] == "call_0"
    assert '"value":64496' in tool_msg["content"].replace(" ", "")


def test_tool_errors_are_fed_back(settings):
    llm = FakeLLM(
        completion(tool_calls=[("run_sql", '{"query": "DROP TABLE events"}'), ("run_sql", "not json")]),
        completion("Sorry."),
    )
    answer, _ = run_ask(settings, llm)
    assert answer == "Sorry."
    contents = [m["content"] for m in llm.requests[1]["messages"] if m["role"] == "tool"]
    assert contents[0].startswith("Error: Only SELECT")
    assert contents[1].startswith("Error: tool arguments are not valid JSON")


def test_long_results_are_truncated(settings, monkeypatch):
    monkeypatch.setattr(chat, "MAX_TOOL_RESULT_CHARS", 50)
    llm = FakeLLM(completion(tool_calls=[("describe_table", "{}")]), completion("ok"))
    run_ask(settings, llm)
    assert llm.requests[1]["messages"][-1]["content"].endswith("[truncated, use a smaller LIMIT]")


def test_step_limit_gives_deterministic_answer(settings):
    s = settings.model_copy(update={"llm_max_steps": 2})
    loop = completion(tool_calls=[("list_tables", "")])
    answer, calls = run_ask(s, FakeLLM(loop, loop))
    assert "No final answer after 2" in answer
    assert len(calls) == 2


def test_to_openai_tools():
    tool = SimpleNamespace(name="t", description=None, input_schema={"type": "object"})
    assert chat.to_openai_tools([tool]) == [
        {"type": "function", "function": {"name": "t", "description": "", "parameters": {"type": "object"}}}
    ]
