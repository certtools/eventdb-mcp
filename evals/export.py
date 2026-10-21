"""Export the golden eventdb as files, for databases this repo does not start itself.

    uv run python -m evals.export --rows 1000 --out tmp/golden

Writes:

* ``golden.sql``: the IntelMQ schema (``tests/fixtures/initdb.sql``) plus every event as a
  ``COPY ... FROM stdin`` block. Load it with ``psql -f golden.sql``, or mount the directory
  at ``/docker-entrypoint-initdb.d`` of the official postgres image.
* ``answers.json``: per question its id, category, kind, prompt and expected answer
  (normalised like the grader compares it), plus rows and seed.

Same generator and seed as ``evals.run``, so the answers match the exported data.
"""

import json
from datetime import datetime
from pathlib import Path

import click

from evals import dataset, pgtmp
from evals.grading import normalise_expected
from evals.questions import QUESTIONS, answer_key

_ESCAPES = str.maketrans({"\\": "\\\\", "\t": "\\t", "\n": "\\n", "\r": "\\r"})


def copy_value(value) -> str:
    """One value in PostgreSQL's COPY text format."""
    if value is None:
        return "\\N"
    if isinstance(value, bool):
        return "t" if value else "f"
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        value = json.dumps(value, ensure_ascii=False)
    return str(value).translate(_ESCAPES)


def golden_sql(events: list[dataset.Event]) -> str:
    """Schema + data as one SQL script."""
    columns = ", ".join(f'"{c}"' for c in dataset.COLUMNS)
    lines = [
        f"-- Golden eventdb: {len(events)} synthetic events (evals/dataset.py). No real data.",
        pgtmp.INITDB_SQL.read_text(encoding="utf-8").rstrip(),
        f"COPY events ({columns}) FROM stdin;",
        *("\t".join(copy_value(v) for v in dataset.to_row(e)) for e in events),
        "\\.",
        "ANALYZE events;",
    ]
    return "\n".join(lines) + "\n"


def answers(events: list[dataset.Event], rows: int, seed: int) -> dict:
    """Questions with their expected answers, JSON-friendly."""
    key = answer_key(events)
    return {
        "rows": rows,
        "seed": seed,
        "questions": [
            {
                "id": q.id,
                "category": q.category,
                "kind": q.kind,
                "text": q.text,
                "prompt": q.prompt(),
                "expected": normalise_expected(key[q.id]),
            }
            for q in QUESTIONS
        ],
    }


@click.command()
@click.option("--rows", default=1000, show_default=True, help="Number of events.")
@click.option("--seed", default=dataset.SEED, show_default=True, help="Dataset seed.")
@click.option("--out", "out_dir", required=True, type=click.Path(path_type=Path), help="Output directory.")
def main(rows: int, seed: int, out_dir: Path) -> None:
    """Write golden.sql and answers.json."""
    events = dataset.generate(rows, seed)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "golden.sql").write_text(golden_sql(events), encoding="utf-8")
    (out_dir / "answers.json").write_text(
        json.dumps(answers(events, rows, seed), indent=2) + "\n", encoding="utf-8"
    )
    click.echo(f"wrote {out_dir / 'golden.sql'} ({rows} events) and {out_dir / 'answers.json'}")


if __name__ == "__main__":
    main()  # pylint: disable=no-value-for-parameter
