# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""The fixture-in/table-out test harness (issue #108, E2.S1) -- see .sdlc/plans/108.md Design
decision F. Shipped inside insight/metrics/ (not insight/tests/) so #109-114's own test files
import it as a normal product module (`from insight.metrics.testing import ...`) rather than
duplicating it per story. No `import duckdb` -- both helpers take an already-open conn/cursor."""
import json


def load_fixture_jsonl(conn, path):
    """Read a tests/fixtures/<id>.jsonl file: one JSON object per line, each carrying a
    reserved '_table' key naming its target table, plus that table's own column names as the
    remaining keys. Inserts each line as one row via a plain
    `INSERT INTO <table> (<its own keys>) VALUES (...)` -- generalizes the raw
    conn.execute("INSERT INTO ...") convention every existing insight/tests/test_store.py test
    already uses, driven by a file instead of inline Python. A line's omitted columns are left
    NULL, exactly like a narrower inline INSERT. Blank lines are skipped. Not a general JSONL
    ingester: no schema validation beyond what the table's own DDL enforces at insert time.

    BLOCKING-2 fix (plan review): a JSON *object* value (Python dict) is json.dumps'd before
    binding. Passed unencoded, DuckDB's own auto-coercion turns a dict into its internal
    STRUCT-literal text (single-quoted keys, unquoted list elements) -- not valid JSON --
    which silently corrupts any fixture value meant for a VARCHAR column holding real JSON
    (config_json, raw_payload). There is no dict/MAP-typed column anywhere in the real schema
    (grep-confirmed against store.py's DDL), so every dict value is unambiguously destined for
    a VARCHAR column as a JSON string. A JSON *array* value (Python list) is left untouched --
    every list-typed column in the real schema is a genuine VARCHAR[] (areas, needs, files,
    every degraded_* column), and passing it straight through is the correct, already-verified
    binding for that case."""
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            table = row.pop("_table")
            columns = list(row.keys())
            values = [json.dumps(v) if isinstance(v, dict) else v for v in row.values()]
            placeholders = ", ".join(["?"] * len(columns))
            conn.execute(
                f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
                values,
            )


def rows_as_dicts(cursor):
    """A DuckDB cursor's result set as a list of {column_name: value} dicts, in column and row
    order -- so a metric test's 'expected' value can be a plain Python literal instead of a
    second on-disk format."""
    columns = [d[0] for d in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]
