"""Reading PostgreSQL query plans to find scans no index could serve.

The naive version of this check -- "fail if the plan contains a Seq Scan" --
does not work, and it is worth saying why, because it shapes everything here.

On a test database of twenty rows PostgreSQL chooses a sequential scan even when
a perfect index exists, because reading twenty rows is cheaper than descending a
B-tree. So a Seq Scan tells you nothing about whether an index is missing, and
thresholding on table size means the check never fires on test data at all.

What we ask instead is whether an index *could* have been used. Run the EXPLAIN
with ``enable_seqscan`` off, which makes the planner treat sequential scans as
enormously expensive. If it still picks one, no index can serve that filter --
and that answer does not depend on how much data the table holds.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

_SEQ_SCAN_NODES = {"Seq Scan", "Parallel Seq Scan"}

# Tokens that look like columns in a filter expression but are not.
_NOT_A_COLUMN = {
    "and",
    "or",
    "not",
    "any",
    "all",
    "true",
    "false",
    "null",
    "is",
    "like",
    "ilike",
    "similar",
    "between",
    "in",
    "case",
    "when",
    "then",
    "else",
    "end",
}

_LITERAL = re.compile(r"'(?:[^']|'')*'")
_CAST = re.compile(r"::\s*[a-z_][a-z0-9_]*(?:\s*\[\s*\])*", re.IGNORECASE)
_PLACEHOLDER = re.compile(r"\$\d+")
# PostgreSQL parenthesises a column before casting it, so a varchar filter
# reads "((city)::text = 'x'::text)". Once the cast is gone the wrapper must go
# too, or the commonest case of all -- a text column -- yields no column name.
# The lookbehind is what keeps "lower(email)" from being unwrapped into
# "lower email", which would suggest an index that does not apply.
_REDUNDANT_PARENS = re.compile(r"(?<![A-Za-z0-9_\"])\(\s*([a-z_][a-z0-9_]*)\s*\)", re.IGNORECASE)

_COLUMN_BEFORE_OPERATOR = re.compile(
    r"\b([a-z_][a-z0-9_]*)\s*(?:<=|>=|<>|!=|=|<|>|~~\*?|!~~\*?|@@|\bIS\b|\bLIKE\b)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SeqScan:
    """A sequential scan the planner kept even with seq scans penalised."""

    relation: str
    filter_expression: str
    columns: tuple[str, ...]
    """Best-effort column names pulled out of the filter, for the index hint."""

    def suggested_index(self) -> str | None:
        """A ``CREATE INDEX`` to try, or None when the filter is too complex.

        Deliberately a suggestion and not a promise: which columns to index, in
        what order, and whether the index earns its write cost are judgement
        calls that need the whole query pattern, not one plan node.
        """
        if not self.columns:
            return None
        return f"CREATE INDEX ON {self.relation} ({', '.join(self.columns)});"

    def describe(self) -> str:
        lines = [f'Seq Scan on "{self.relation}"  Filter: {self.filter_expression}']
        hint = self.suggested_index()
        if hint:
            lines.append(f"      try:  {hint}")
        return "\n".join(lines)


def parse(payload: Any) -> tuple[SeqScan, ...]:
    """Pull every filtered sequential scan out of an ``EXPLAIN (FORMAT JSON)`` result.

    Accepts the payload however the driver hands it over: already-decoded JSON,
    or a string still to decode.
    """
    document = _decode(payload)
    if document is None:
        return ()

    found: list[SeqScan] = []
    for node in _walk(document):
        if node.get("Node Type") not in _SEQ_SCAN_NODES:
            continue
        expression = node.get("Filter")
        if not expression:
            # An unfiltered sequential scan is a deliberate full read of the
            # table. No index would help, and none is missing.
            continue
        relation = node.get("Relation Name") or node.get("Alias") or "?"
        found.append(
            SeqScan(
                relation=str(relation),
                filter_expression=str(expression),
                columns=filter_columns(str(expression)),
            )
        )
    return tuple(found)


def filter_columns(expression: str) -> tuple[str, ...]:
    """Column names appearing on the left of a comparison, in order, deduplicated.

    A heuristic on a string PostgreSQL meant for humans, so it gives up rather
    than guesses: a filter over a function call such as ``lower(email) = $1``
    yields nothing, and the caller shows the raw expression instead.
    """
    cleaned = _LITERAL.sub("''", expression)
    cleaned = _CAST.sub("", cleaned)
    cleaned = _PLACEHOLDER.sub("$", cleaned)
    cleaned = _REDUNDANT_PARENS.sub(r"\1", cleaned)

    seen: dict[str, None] = {}
    for match in _COLUMN_BEFORE_OPERATOR.finditer(cleaned):
        name = match.group(1)
        if name.lower() in _NOT_A_COLUMN:
            continue
        # A name immediately followed by "(" is a function, not a column.
        if cleaned[match.end(1) : match.end(1) + 1] == "(":
            continue
        seen.setdefault(name, None)
    return tuple(seen)


def _decode(payload: Any) -> Any:
    if isinstance(payload, (str, bytes, bytearray)):
        try:
            return json.loads(payload)
        except (ValueError, TypeError):
            return None
    return payload


def _walk(document: Any) -> Iterator[dict[str, Any]]:
    """Every plan node in the document, depth first.

    ``EXPLAIN (FORMAT JSON)`` returns a list of statements, each with a "Plan";
    nodes nest under "Plans". Subplans and CTEs land there too, so one recursive
    walk reaches all of them.
    """
    if isinstance(document, list):
        for entry in document:
            yield from _walk(entry)
        return
    if not isinstance(document, dict):
        return
    if "Plan" in document:
        yield from _walk(document["Plan"])
        return
    yield document
    for child in document.get("Plans") or ():
        yield from _walk(child)
