"""Collecting queries and answering questions about them."""

from __future__ import annotations

import os
import sys
from collections import Counter
from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING

from pytest_querycount.records import Duplicate, QueryRecord

if TYPE_CHECKING:
    from types import FrameType

# Frames belonging to these files are never the interesting caller: they are the
# ORM, the driver, the test runner, or us.
_INTERNAL_PATHS = (
    "/sqlalchemy/",
    "/pytest_querycount/",
    "/_pytest/",
    "/pluggy/",
    "/asyncio/",
    "/greenlet/",
    "/contextlib.py",
    "/threading.py",
)

_MAX_STACK_DEPTH = 60


def _shorten(path: str) -> str:
    """Relative to the working directory when that is shorter, absolute otherwise.

    Failure messages are read in a terminal, and an absolute path to a pytest
    temp directory pushes the useful part off the edge of the screen.
    """
    try:
        relative = os.path.relpath(path)
    except ValueError:  # pragma: no cover - different drive on Windows
        return path
    return path if relative.startswith("..") else relative


def caller_location(skip: int = 1) -> str | None:
    """``path:lineno`` of the closest frame that is not library machinery."""
    try:
        frame: FrameType | None = sys._getframe(skip + 1)
    except ValueError:  # pragma: no cover - stack shallower than `skip`
        return None
    depth = 0
    while frame is not None and depth < _MAX_STACK_DEPTH:
        filename = frame.f_code.co_filename.replace(os.sep, "/")
        if not any(part in filename for part in _INTERNAL_PATHS):
            return f"{_shorten(frame.f_code.co_filename)}:{frame.f_lineno}"
        frame = frame.f_back
        depth += 1
    return None


class Recorder:
    """Accumulates :class:`QueryRecord` objects and reports on them.

    A recorder only sees queries while it is on the active stack; see
    :mod:`pytest_querycount.backends`.
    """

    def __init__(self, explain: bool = False) -> None:
        self.records: list[QueryRecord] = []
        self.explain = explain
        """Whether the backend should obtain a query plan for each SELECT.

        Off unless a check needs it: EXPLAIN means a second round trip per
        query, which is a real cost to impose on a suite that did not ask."""

    def add(self, record: QueryRecord) -> None:
        self.records.append(record)

    def clear(self) -> None:
        self.records.clear()

    # -- basic numbers ----------------------------------------------------

    def __len__(self) -> int:
        return len(self.records)

    @property
    def count(self) -> int:
        """How many statements were executed."""
        return len(self.records)

    @property
    def total_duration(self) -> float:
        """Seconds spent in the driver, summed."""
        return sum(record.duration for record in self.records)

    def of_kind(self, *kinds: str) -> list[QueryRecord]:
        wanted = {kind.lower() for kind in kinds}
        return [record for record in self.records if record.kind in wanted]

    # -- duplicate analysis ----------------------------------------------

    @property
    def fingerprints(self) -> Counter[str]:
        return Counter(record.fingerprint for record in self.records)

    def duplicates(
        self,
        threshold: int = 2,
        kinds: Sequence[str] | None = ("select",),
    ) -> list[Duplicate]:
        """Query shapes that ran at least ``threshold`` times.

        ``kinds`` restricts the search to certain statement types; ``None``
        considers all of them. The default looks only at SELECTs, because a
        repeated SELECT is the N+1 signature, whereas repeated SAVEPOINTs and
        BEGINs are simply how transactions work.
        """
        candidates: Iterable[QueryRecord]
        candidates = self.records if kinds is None else self.of_kind(*kinds)

        counts: Counter[str] = Counter()
        samples: dict[str, QueryRecord] = {}
        locations: dict[str, Counter[str]] = {}
        for record in candidates:
            counts[record.fingerprint] += 1
            samples.setdefault(record.fingerprint, record)
            if record.location:
                locations.setdefault(record.fingerprint, Counter())[record.location] += 1

        found = [
            Duplicate(
                fingerprint=shape,
                count=hits,
                sample=samples[shape],
                locations=locations.get(shape, Counter()),
            )
            for shape, hits in counts.items()
            if hits >= threshold
        ]
        found.sort(key=lambda duplicate: duplicate.count, reverse=True)
        return found

    # -- query plans ------------------------------------------------------

    @property
    def explained_count(self) -> int:
        return sum(1 for record in self.records if record.explained)

    def seq_scans(self, ignore: Sequence[str] = ()) -> list[QueryRecord]:
        """Records whose plan held a sequential scan no index could serve.

        ``ignore`` names tables to pass over -- a small lookup table is read
        whole on purpose, and no index would improve it.
        """
        skipped = {name.lower() for name in ignore}
        return [
            record
            for record in self.records
            if any(scan.relation.lower() not in skipped for scan in record.seq_scans)
        ]

    # -- human output -----------------------------------------------------

    def report(self, width: int = 100, limit: int | None = None, collapse: bool = True) -> str:
        """The recorded queries, in order, with timings and origins.

        With ``collapse`` a run of identical shapes prints as one line -- twelve
        copies of the same SELECT say nothing that ``12x`` does not, and they
        push the useful lines off the screen.
        """
        if not self.records:
            return "no queries recorded"

        groups = self._runs() if collapse else [[record] for record in self.records]
        shown = groups if limit is None else groups[:limit]

        lines = [
            f"{self.count} quer{'y' if self.count == 1 else 'ies'} "
            f"in {self.total_duration * 1000:.1f}ms"
        ]
        position = 1
        for group in shown:
            first = group[0]
            elapsed = sum(record.duration for record in group) * 1000
            if len(group) == 1:
                label = f"{position}."
                timing = f"[{elapsed:7.2f}ms]"
            else:
                label = f"{position}-{position + len(group) - 1}."
                timing = f"[{elapsed:7.2f}ms] {len(group)}x"
            marker = " (executemany)" if first.executemany else ""
            lines.append(f"  {label:>8} {timing}{marker} {first.short_sql(width)}")
            if first.location:
                lines.append(f"  {'':>8} {'':>10}   from {first.location}")
            position += len(group)

        hidden = self.count - sum(len(group) for group in shown)
        if hidden > 0:
            lines.append(f"  ... and {hidden} more")
        return "\n".join(lines)

    def _runs(self) -> list[list[QueryRecord]]:
        """Consecutive records sharing a fingerprint, grouped."""
        runs: list[list[QueryRecord]] = []
        for record in self.records:
            if runs and runs[-1][0].fingerprint == record.fingerprint:
                runs[-1].append(record)
            else:
                runs.append([record])
        return runs

    def __repr__(self) -> str:
        return f"<Recorder {self.count} queries, {self.total_duration * 1000:.1f}ms>"
