"""The data the recorder collects."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from pytest_querycount.explain import SeqScan


@dataclass(frozen=True)
class QueryRecord:
    """One statement handed to the driver."""

    sql: str
    """The statement as the driver saw it, literals and placeholders intact."""

    fingerprint: str
    """``sql`` with its literals replaced -- see :func:`~.normalize.fingerprint`."""

    kind: str
    """Leading keyword, lowercased: ``"select"``, ``"insert"``, ..."""

    duration: float
    """Seconds spent inside the driver call."""

    executemany: bool
    """True when one call carried many parameter sets. Counts as a single query,
    because it is a single round trip -- which is exactly why it is the fix for
    a loop of inserts."""

    rowcount: int | None = None

    seq_scans: tuple[SeqScan, ...] = ()
    """Sequential scans the planner kept even with seq scans penalised, i.e. ones
    no index could serve. Only populated when a check asked for EXPLAIN."""

    explained: bool = False
    """Whether a plan was actually obtained. Distinguishes "no problems found"
    from "we never looked", which must not be reported the same way."""

    location: str | None = None
    """``path:lineno`` of the nearest frame outside the ORM and the test runner,
    i.e. the line of *your* code that caused this."""

    def short_sql(self, width: int = 100) -> str:
        collapsed = " ".join(self.sql.split())
        if len(collapsed) <= width:
            return collapsed
        return collapsed[: width - 1] + "…"


@dataclass(frozen=True)
class Duplicate:
    """A query shape that ran more than once."""

    fingerprint: str
    count: int
    sample: QueryRecord
    locations: Counter[str] = field(default_factory=Counter)

    def describe(self, width: int = 100) -> str:
        lines = [f"{self.count}x  {self.sample.short_sql(width)}"]
        for location, hits in self.locations.most_common(3):
            lines.append(f"      from {location} ({hits}x)")
        return "\n".join(lines)
