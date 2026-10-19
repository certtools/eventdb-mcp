"""Golden-question eval and benchmark runner.

    uv run python -m evals.run                     # 1000 rows, verify key, ask the LLM (.env)
    uv run python -m evals.run --no-llm            # only verify the answer key (fast, no LLM)
    uv run python -m evals.run --rows 1000000 --no-llm   # release benchmark

Steps: generate events -> answer key in Python -> throwaway PostgreSQL ->
COPY events -> reference SQL must match the key (else exit 1) -> optionally ask
the LLM every question through the MCP server -> grade -> report.

Artifacts in ``logs/evals/``: ``<run>.json`` (all answers + metrics),
``<run>.md`` (summary), ``<run>.log`` (progress: successes, failures, rate, ETA).
"""

import asyncio
import json
import logging
import subprocess  # nosec B404 - only "git rev-parse"
import time
from datetime import UTC, date, datetime
from pathlib import Path

import click
import psycopg
from psycopg.types.json import Jsonb
from rich.console import Console
from rich.table import Table

from evals import dataset, pgtmp
from evals.grading import Grade, Record, grade, summarize
from evals.questions import BY_ID, QUESTIONS, AmbiguousError, Question, answer_key
from eventdb_mcp import chat, db
from eventdb_mcp.config import Settings
from eventdb_mcp.server import create_server

console = Console(stderr=True)
log = logging.getLogger("evals")


class Progress:  # pylint: disable=too-few-public-methods
    """Logs done/total, successes, failures, rate and ETA (AGENTS.md: long tasks log progress)."""

    def __init__(self, what: str, total: int, unit: str):
        self.what, self.total, self.unit = what, total, unit
        self.done = self.ok = self.failed = 0
        self.start = time.monotonic()

    def step(self, n: int = 1, ok: bool = True) -> None:
        """Record ``n`` finished items and log the progress line."""
        self.done += n
        self.ok += n if ok else 0
        self.failed += 0 if ok else n
        elapsed = time.monotonic() - self.start
        rate = self.done / elapsed if elapsed else 0.0
        eta = (self.total - self.done) / rate if rate else 0.0
        log.info("%s %d/%d ok=%d failed=%d %.1f %s/s ETA %.0fs",
                 self.what, self.done, self.total, self.ok, self.failed, rate, self.unit, eta)  # fmt: skip


def load(su_dsn: str, events: list[dataset.Event]) -> float:
    """COPY ``events`` into the events table; returns rows/s."""
    columns = ", ".join(f'"{c}"' for c in dataset.COLUMNS)
    progress = Progress("load", len(events), "rows")
    extra_at = dataset.COLUMNS.index("extra")
    with psycopg.connect(su_dsn) as conn, conn.cursor() as cur:
        with cur.copy(f"COPY events ({columns}) FROM STDIN") as copy:  # pyright: ignore
            for i, event in enumerate(events, 1):
                row = list(dataset.to_row(event))
                row[extra_at] = Jsonb(row[extra_at]) if row[extra_at] is not None else None
                copy.write_row(row)
                if i % 50_000 == 0 or i == len(events):
                    progress.step(50_000 if i % 50_000 == 0 else i % 50_000)
        cur.execute("ANALYZE events")
    return len(events) / (time.monotonic() - progress.start)


def as_answer(question: Question, rows: list[dict]) -> str:
    """Format a reference-SQL result like an LLM answer line, so the same grader applies."""
    values = [next(iter(r.values())) for r in rows]
    if question.kind == "set":
        return "ANSWER: " + ", ".join(str(v) for v in values)
    value = values[0]
    if question.kind == "bool":
        return "ANSWER: " + ("yes" if value else "no")
    return f"ANSWER: {value.isoformat() if isinstance(value, date) else value}"


def verify(settings: Settings, key: dict) -> list[dict]:
    """Run every reference query; each must match the Python answer key."""
    results = []
    for q in QUESTIONS:
        started = time.monotonic()
        rows = db.run_query(settings, q.sql, max_rows=10_000)["rows"]
        seconds = time.monotonic() - started
        result = grade(q, key[q.id], as_answer(q, rows))
        results.append({"qid": q.id, "ok": result.outcome == "correct", "seconds": seconds,
                        "sql_answer": as_answer(q, rows)[8:]})  # fmt: skip
        log.info("verify %s %s %.3fs", q.id, result.outcome, seconds)
    return results


async def ask_all(settings: Settings, questions: list[Question], key: dict, repeats: int) -> list[Record]:  # pylint: disable=too-many-locals
    """Ask every question ``repeats`` times through the MCP server and grade the replies."""
    server = create_server(settings)
    progress = Progress("ask", len(questions) * repeats, "questions")
    records = []
    for repeat in range(1, repeats + 1):
        for q in questions:
            try:  # infrastructure failure must abort the run, not be graded as a wrong answer
                db.run_query(settings, "SELECT 1")
            except db.QueryError as exc:
                raise click.ClickException(
                    f"database unreachable before {q.id}, results invalid: {exc}"
                ) from exc
            calls: list = []
            started = time.monotonic()
            try:
                reply = await chat.ask(
                    q.prompt(), settings, server, on_tool_call=lambda n, a, calls=calls: calls.append((n, a))
                )
                result, error = grade(q, key[q.id], reply), ""
            except Exception as exc:  # pylint: disable=broad-exception-caught  # an eval must not stop
                reply, result, error = "", Grade("error"), f"{type(exc).__name__}: {exc}"
            record = Record(q.id, q.category, q.kind, repeat, key[q.id], reply, result, calls,
                            time.monotonic() - started, error)  # fmt: skip
            records.append(record)
            progress.step(ok=result.outcome == "correct")
            console.print(f"[{'green' if result.outcome == 'correct' else 'red'}]{q.id} r{repeat} "
                          f"{result.outcome}[/] expected={record.as_dict()['expected']} got={result.parsed} "
                          f"tools={len(calls)} {record.seconds:.0f}s")  # fmt: skip
    return records


def markdown(meta: dict, verification: list[dict], metrics: dict | None, records: list[Record]) -> str:
    """Human-readable summary of a run."""
    lines = [f"# Eval run {meta['run']}", "", *(f"- **{k}**: {v}" for k, v in meta.items()), ""]
    lines += ["## Answer key verification (Python vs. reference SQL)", "",
              "| question | match | SQL seconds | answer |", "|---|---|---|---|"]  # fmt: skip
    lines += [f"| {v['qid']} | {'✓' if v['ok'] else '✗'} | {v['seconds']:.3f} | {v['sql_answer'][:60]} |"
              for v in verification]  # fmt: skip
    if metrics:
        lines += ["", "## Metrics", "", "```json", json.dumps(metrics, indent=2, default=str), "```", ""]
        lines += [
            "## Answers",
            "",
            "| q | repeat | outcome | expected | parsed | tools | s |",
            "|---|---|---|---|---|---|---|",
        ]
        for r in records:
            expected = r.as_dict()["expected"]
            cells = [
                r.qid,
                r.repeat,
                r.grade.outcome,
                expected,
                r.grade.parsed,
                len(r.tool_calls),
                f"{r.seconds:.0f}",
            ]
            lines.append("| " + " | ".join(str(c) for c in cells) + " |")
    return "\n".join(lines) + "\n"


def _git_commit() -> str:
    result = subprocess.run(  # nosec B603 B607
        ["git", "rev-parse", "--short", "HEAD"],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() or "unknown"


@click.command()
@click.option("--rows", default=1000, show_default=True, help="Number of events in the golden eventdb.")
@click.option("--seed", default=dataset.SEED, show_default=True, help="Dataset seed.")
@click.option("--repeats", default=1, show_default=True, help="Ask every question this many times.")
@click.option("--questions", "qids", default="", help="Comma-separated subset, e.g. q01,q06.")
@click.option("--no-llm", is_flag=True, help="Only build the DB and verify the answer key (benchmark).")
@click.option(
    "--env-file", default=".env", show_default=True, help="LLM_* settings (EVENTDB_DSN is ignored)."
)
@click.option("--out-dir", default="logs/evals", show_default=True, type=click.Path(path_type=Path))
def main(rows, seed, repeats, qids, no_llm, env_file, out_dir):  # pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals
    """Build the golden eventdb, verify the answer key and (optionally) evaluate the LLM."""
    if not pgtmp.available():
        raise click.ClickException("initdb/pg_ctl/psql not found on PATH")
    out_dir.mkdir(parents=True, exist_ok=True)
    llm_settings = None if no_llm else Settings.from_env(env_file)
    run_id = (
        datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        + f"_{rows}"
        + ("" if no_llm else f"_{llm_settings.llm_model}")
    )
    run_id = run_id.replace("/", "_").replace(":", "-")
    handler = logging.FileHandler(out_dir / f"{run_id}.log")
    handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(message)s"))
    for name in ("evals", "eventdb_mcp"):
        logging.getLogger(name).addHandler(handler)
        logging.getLogger(name).setLevel(logging.INFO)

    started = time.monotonic()
    events = dataset.generate(rows, seed)
    try:
        key = answer_key(events)
    except AmbiguousError as exc:
        raise click.ClickException(f"seed {seed} gives a tie ({exc}); try another --seed") from exc
    generate_s = time.monotonic() - started
    console.print(f"generated {rows} events + answer key in {generate_s:.1f}s")

    with pgtmp.cluster(f"eval-pg-{rows}") as base:
        su_dsn, ro_dsn = pgtmp.create_eventdb(base, "golden")
        rate = load(su_dsn, events)
        console.print(f"loaded {rows} rows at {rate:,.0f} rows/s")
        db_settings = (llm_settings or Settings()).model_copy(
            update={"db_dsn": ro_dsn, "allow_superuser": False, "statement_timeout_ms": 600_000}
        )
        verification = verify(db_settings, key)
        bad = [v["qid"] for v in verification if not v["ok"]]
        records, metrics = [], None
        if not bad and not no_llm:
            questions = [BY_ID[q] for q in qids.split(",")] if qids else QUESTIONS
            records = asyncio.run(ask_all(db_settings, questions, key, repeats))
            metrics = summarize(records)

    meta = {
        "run": run_id, "rows": rows, "seed": seed, "repeats": repeats, "git_commit": _git_commit(),
        "model": llm_settings.llm_model if llm_settings else None,
        "llm_base_url": llm_settings.llm_base_url if llm_settings else None,
        "load_rows_per_s": round(rate), "generate_s": round(generate_s, 1),
    }  # fmt: skip
    report = {
        "meta": meta,
        "verification": verification,
        "metrics": metrics,
        "records": [r.as_dict() for r in records],
    }
    (out_dir / f"{run_id}.json").write_text(json.dumps(report, indent=2, default=str))
    (out_dir / f"{run_id}.md").write_text(markdown(meta, verification, metrics, records))
    _print_summary(verification, metrics)
    console.print(f"report: {out_dir / run_id}.json / .md")
    if bad:
        raise click.ClickException(f"answer key and reference SQL disagree for {', '.join(bad)}")


def _print_summary(verification: list[dict], metrics: dict | None) -> None:
    slowest = sorted(verification, key=lambda v: v["seconds"], reverse=True)[:3]
    timings = ", ".join(f"{v['qid']} {v['seconds']:.2f}s" for v in slowest)
    ok = sum(v["ok"] for v in verification)
    console.print(f"answer key verified: {ok}/{len(verification)}; slowest SQL: {timings}")
    if not metrics:
        return
    tools = metrics["tools"]
    table = Table(title="LLM eval")
    table.add_column("metric")
    table.add_column("value")
    rows = {
        "accuracy": f"{metrics['accuracy']:.1%} ({metrics['outcomes']})",
        "by category": ", ".join(f"{k} {v:.0%}" for k, v in metrics["accuracy_by_category"].items()),
        "set F1 macro / micro": f"{metrics['set']['macro']['f1']:.2f} / {metrics['set']['micro']['f1']:.2f}",
        "bool P / R / F1": " / ".join(f"{metrics['bool'][k]:.2f}" for k in ("precision", "recall", "f1")),
        "bool confusion": str(metrics["bool"]["confusion_matrix"]),
        "int MAPE": str(metrics["int"]["mape"]),
        "tool use / no-tool answers": f"{tools['used_any']:.0%} / {tools['answered_without_tools']}",
        "latency mean / p95": f"{metrics['latency_s']['mean']:.0f}s / {metrics['latency_s']['p95']:.0f}s",
        "safety": str(metrics["safety"]),
    }
    for name, value in rows.items():
        table.add_row(name, value)
    console.print(table)


if __name__ == "__main__":
    main()  # pylint: disable=no-value-for-parameter
