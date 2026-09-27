"""Turning a recorder plus a budget into a pass or a failure."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from pytest_querycount import backends
from pytest_querycount.errors import (
    DuplicateQueryError,
    ExplainUnavailableError,
    NoBackendError,
    SeqScanError,
    TooManyQueriesError,
)
from pytest_querycount.recorder import Recorder
from pytest_querycount.records import Duplicate, QueryRecord

DEFAULT_DUPLICATE_THRESHOLD = 2
DEFAULT_KINDS = ("select",)


@dataclass
class Budget:
    """What a test is allowed to do to the database."""

    max_queries: int | None = None
    no_n_plus_one: bool = False
    duplicate_threshold: int = DEFAULT_DUPLICATE_THRESHOLD
    kinds: Sequence[str] | None = DEFAULT_KINDS
    no_seq_scan: bool = False
    ignore_tables: Sequence[str] = ()

    @property
    def is_empty(self) -> bool:
        return self.max_queries is None and not self.no_n_plus_one and not self.no_seq_scan

    @property
    def needs_explain(self) -> bool:
        """Whether the backend must pay for a query plan per SELECT."""
        return self.no_seq_scan


def enforce(budget: Budget, recorder: Recorder, label: str = "This test") -> None:
    """Raise if ``recorder`` violated ``budget``. Cheapest checks first."""
    # Keep our own frames out of the traceback. The failure message is the
    # product here; a wall of plugin internals above it is noise.
    __tracebackhide__ = True

    if budget.is_empty:
        return

    if not backends.installed():
        raise NoBackendError(
            "pytest-querycount has nothing to watch, so this query budget would "
            "always pass.\n"
            "Install a supported backend:  pip install 'pytest-querycount[sqlalchemy]'\n"
            "If your project uses a library we do not support yet, please open an issue."
        )

    if budget.max_queries is not None and recorder.count > budget.max_queries:
        raise TooManyQueriesError(_too_many_message(budget, recorder, label))

    if budget.no_n_plus_one:
        duplicates = recorder.duplicates(
            threshold=budget.duplicate_threshold,
            kinds=budget.kinds,
        )
        if duplicates:
            raise DuplicateQueryError(_duplicate_message(duplicates, recorder, label))

    if budget.no_seq_scan:
        _enforce_no_seq_scan(budget, recorder, label)


def _enforce_no_seq_scan(budget: Budget, recorder: Recorder, label: str) -> None:
    __tracebackhide__ = True

    selects = recorder.of_kind("select")
    if selects and not recorder.explained_count:
        # Nothing was explained, so the check saw nothing and would pass no
        # matter what the queries did. Say so instead.
        raise ExplainUnavailableError(
            "no_seq_scan could not obtain a query plan, so it would pass "
            "regardless of what the queries do.\n"
            "This check needs PostgreSQL: it reads EXPLAIN (FORMAT JSON), which "
            "SQLite and MySQL do not provide in a comparable form.\n"
            "Run these tests against PostgreSQL, or drop the marker."
        )

    offenders = recorder.seq_scans(ignore=budget.ignore_tables)
    if offenders:
        raise SeqScanError(_seq_scan_message(offenders, budget, label))


def _seq_scan_message(
    offenders: Sequence[QueryRecord],
    budget: Budget,
    label: str,
) -> str:
    skipped = {name.lower() for name in budget.ignore_tables}
    lines = [
        f"{label} ran {len(offenders)} quer"
        f"{'y' if len(offenders) == 1 else 'ies'} that no index could serve.",
        "",
        "PostgreSQL still chose a sequential scan with enable_seqscan disabled, "
        "which means no index covers the filtered columns.",
        "",
    ]
    for record in offenders[:5]:
        for scan in record.seq_scans:
            if scan.relation.lower() in skipped:
                continue
            lines.append(f"  {scan.describe()}")
        if record.location:
            lines.append(f"      from {record.location}")
        lines.append(f"      {record.short_sql(110)}")
        lines.append("")
    if len(offenders) > 5:
        lines.append(f"  ... and {len(offenders) - 5} more")
    lines.append(
        "If a scan is intentional -- a small lookup table read whole -- exclude it "
        'with @pytest.mark.no_seq_scan(ignore=("table_name",)).'
    )
    return "\n".join(lines)


def _too_many_message(budget: Budget, recorder: Recorder, label: str) -> str:
    assert budget.max_queries is not None
    over = recorder.count - budget.max_queries
    lines = [
        f"{label} ran {recorder.count} queries, budget is {budget.max_queries} ({over} over).",
    ]

    # Lead with the diagnosis. Someone reading a CI log wants the culprit in the
    # first few lines, not after a list of thirteen SELECTs.
    duplicates = recorder.duplicates(threshold=2, kinds=budget.kinds)
    if duplicates:
        lines += [
            "",
            "Repeated query shapes -- most likely where the extra queries come from:",
            *(f"  {duplicate.describe()}" for duplicate in duplicates[:3]),
        ]

    lines += ["", recorder.report(limit=12)]
    return "\n".join(lines)


def _duplicate_message(
    duplicates: Sequence[Duplicate],
    recorder: Recorder,
    label: str,
) -> str:
    worst = duplicates[0]
    lines = [
        f"{label} repeated the same query {worst.count} times ({recorder.count} queries in total).",
        "",
        "This is the N+1 pattern. Load the related rows in one go instead -- with "
        "selectinload() or joinedload() for a relationship, or a single IN query.",
        "",
    ]
    for duplicate in duplicates[:5]:
        lines.append(f"  {duplicate.describe()}")
    if len(duplicates) > 5:
        lines.append(f"  ... and {len(duplicates) - 5} more repeated shapes")
    return "\n".join(lines)
