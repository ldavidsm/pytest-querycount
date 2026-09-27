"""pytest integration: options, markers, the fixture, and the summary."""

from __future__ import annotations

from collections.abc import Generator, Sequence
from pathlib import Path
from typing import Any

import pytest

from pytest_querycount import backends, checks, report, writer
from pytest_querycount.checks import DEFAULT_DUPLICATE_THRESHOLD, DEFAULT_KINDS, Budget
from pytest_querycount.recorder import Recorder

RECORDER_KEY = pytest.StashKey[Recorder]()
_STATS: dict[str, report.TestStats] = {}

# path -> {qualname: observed count}, gathered for --querycount-write-budgets.
_OBSERVED: dict[Path, dict[str, int]] = {}


# -- configuration ---------------------------------------------------------


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("querycount", "SQL query budgets")
    group.addoption(
        "--querycount-report",
        action="store_true",
        default=False,
        help="Print a table of the tests that ran the most SQL queries.",
    )
    group.addoption(
        "--querycount-top",
        type=int,
        default=10,
        metavar="N",
        help="How many tests the report lists (default: 10).",
    )
    group.addoption(
        "--querycount-max",
        type=int,
        default=None,
        metavar="N",
        help="Apply a query budget of N to every test that has no explicit one.",
    )
    group.addoption(
        "--querycount-write-budgets",
        action="store_true",
        default=False,
        help=(
            "Write @pytest.mark.max_queries markers into your test files using "
            "the counts observed in this run, then exit without enforcing them. "
            "MODIFIES YOUR FILES -- review the diff afterwards."
        ),
    )
    group.addoption(
        "--querycount-no-seq-scan",
        action="store_true",
        default=False,
        help=(
            "Apply the missing-index check to every test. Costs one EXPLAIN per "
            "SELECT, so prefer the marker once you know where to look."
        ),
    )
    parser.addini(
        "querycount_max",
        help="Default query budget for tests without an explicit one.",
        default="",
    )
    parser.addini(
        "querycount_duplicate_threshold",
        help=(
            "How many repeats of one query shape count as N+1 "
            f"(default: {DEFAULT_DUPLICATE_THRESHOLD})."
        ),
        default="",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "max_queries(n): fail the test if it runs more than n SQL queries.",
    )
    config.addinivalue_line(
        "markers",
        "no_n_plus_one(threshold=2, kinds=('select',)): fail the test if one "
        "query shape is executed repeatedly.",
    )
    config.addinivalue_line(
        "markers",
        "no_seq_scan(ignore=()): fail the test if a query scans a table "
        "sequentially because no index could serve its filter. PostgreSQL only.",
    )
    _STATS.clear()
    _OBSERVED.clear()
    backends.install_all()


def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    """Reject malformed markers while collecting.

    Validating here rather than mid-test means a typo is a clean usage error
    instead of an exception raised from inside a hook wrapper.
    """
    for item in items:
        marker = item.get_closest_marker("max_queries")
        if marker is not None:
            _max_queries_from_marker(item, marker)


# -- per-test recording ----------------------------------------------------


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item: pytest.Item) -> Generator[None, object, object]:
    """Record the call phase, then hold the test to its budget.

    Deliberately wraps only the call phase: fixture setup and teardown run
    outside it, so schema creation and seeding never consume a budget.
    """
    __tracebackhide__ = True

    budget = _budget_for(item)
    recorder = Recorder(explain=budget.needs_explain)
    item.stash[RECORDER_KEY] = recorder
    backends.push(recorder)
    try:
        result = yield
    except BaseException:
        # The test itself failed. Record what we saw for the report, but let its
        # exception through untouched: a budget complaint on top of a real
        # failure buries the thing that actually needs fixing.
        _record_stats(item, recorder)
        raise
    finally:
        backends.pop(recorder)

    _record_stats(item, recorder)

    if item.config.getoption("querycount_write_budgets"):
        _observe(item, recorder)
        return result

    checks.enforce(budget, recorder, label=item.name)
    return result


def _record_stats(item: pytest.Item, recorder: Recorder) -> None:
    if not recorder.count:
        return
    duplicates = recorder.duplicates(threshold=2, kinds=DEFAULT_KINDS)
    _STATS[item.nodeid] = report.TestStats(
        nodeid=item.nodeid,
        count=recorder.count,
        duration=recorder.total_duration,
        worst_duplicate=duplicates[0].count if duplicates else 0,
        worst_sql=duplicates[0].sample.short_sql() if duplicates else None,
    )


def _observe(item: pytest.Item, recorder: Recorder) -> None:
    """Remember what a passing test ran, so a marker can be written for it.

    Only passing tests: a test that failed part way through ran an arbitrary
    number of queries, and freezing that number as a budget would be nonsense.
    """
    if not recorder.count:
        return
    function = getattr(item, "function", None)
    qualname = getattr(function, "__qualname__", None)
    if qualname is None:
        return

    per_file = _OBSERVED.setdefault(Path(str(item.path)), {})
    # Parametrised cases share one function, so the widest run wins.
    per_file[qualname] = max(per_file.get(qualname, 0), recorder.count)


def _budget_for(item: pytest.Item) -> Budget:
    """Resolve the budget: marker first, then --querycount-max, then ini."""
    budget = Budget(
        max_queries=_default_max(item.config),
        duplicate_threshold=_default_threshold(item.config),
        no_seq_scan=bool(item.config.getoption("querycount_no_seq_scan")),
    )

    marker = item.get_closest_marker("max_queries")
    if marker is not None:
        budget.max_queries = _max_queries_from_marker(item, marker)

    marker = item.get_closest_marker("no_seq_scan")
    if marker is not None:
        budget.no_seq_scan = True
        budget.ignore_tables = tuple(marker.kwargs.get("ignore", ()))

    marker = item.get_closest_marker("no_n_plus_one")
    if marker is not None:
        budget.no_n_plus_one = True
        budget.duplicate_threshold = int(marker.kwargs.get("threshold", budget.duplicate_threshold))
        if "kinds" in marker.kwargs:
            kinds = marker.kwargs["kinds"]
            budget.kinds = None if kinds is None else tuple(kinds)

    return budget


def _max_queries_from_marker(item: pytest.Item, marker: pytest.Mark) -> int:
    if marker.args:
        value = marker.args[0]
    elif "n" in marker.kwargs:
        value = marker.kwargs["n"]
    else:
        raise pytest.UsageError(
            f"{item.nodeid}: @pytest.mark.max_queries needs a number, "
            "e.g. @pytest.mark.max_queries(3)"
        )
    try:
        return int(value)
    except (TypeError, ValueError):
        raise pytest.UsageError(
            f"{item.nodeid}: @pytest.mark.max_queries({value!r}) is not a number"
        ) from None


def _default_max(config: pytest.Config) -> int | None:
    from_cli = config.getoption("querycount_max")
    if from_cli is not None:
        return int(from_cli)
    from_ini = str(config.getini("querycount_max") or "").strip()
    return int(from_ini) if from_ini else None


def _default_threshold(config: pytest.Config) -> int:
    from_ini = str(config.getini("querycount_duplicate_threshold") or "").strip()
    return int(from_ini) if from_ini else DEFAULT_DUPLICATE_THRESHOLD


# -- the fixture -----------------------------------------------------------


class _Block:
    """Context manager returned by ``querycount(...)``.

    ``__enter__`` yields the :class:`Recorder`, so the same object carries the
    live count during the block and the evidence afterwards.
    """

    def __init__(self, budget: Budget, label: str) -> None:
        self._budget = budget
        self._label = label
        self.recorder = Recorder(explain=budget.needs_explain)

    def __enter__(self) -> Recorder:
        backends.push(self.recorder)
        return self.recorder

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        __tracebackhide__ = True

        backends.pop(self.recorder)
        # An exception from inside the block is the more important news.
        if exc_type is None:
            checks.enforce(self._budget, self.recorder, label=self._label)


@pytest.fixture
def querycount(request: pytest.FixtureRequest) -> Any:
    """Record the queries a block of code runs, and optionally budget them.

        def test_detail(client, querycount):
            with querycount(max_queries=2) as queries:
                client.get("/users/1")
            assert queries.duplicates() == []

    Called with no arguments it only observes, which is the way to find out what
    a budget should be before you commit to one.
    """

    def factory(
        max_queries: int | None = None,
        no_n_plus_one: bool = False,
        threshold: int = DEFAULT_DUPLICATE_THRESHOLD,
        kinds: Sequence[str] | None = DEFAULT_KINDS,
        no_seq_scan: bool = False,
        ignore: Sequence[str] = (),
    ) -> _Block:
        return _Block(
            Budget(
                max_queries=max_queries,
                no_n_plus_one=no_n_plus_one,
                duplicate_threshold=threshold,
                kinds=kinds,
                no_seq_scan=no_seq_scan,
                ignore_tables=tuple(ignore),
            ),
            label="This querycount block",
        )

    return factory


# -- summary ---------------------------------------------------------------


def pytest_terminal_summary(
    terminalreporter: Any,
    exitstatus: int,
    config: pytest.Config,
) -> None:
    if config.getoption("querycount_write_budgets"):
        _write_budgets(terminalreporter)
        return

    if not config.getoption("querycount_report"):
        return
    lines = report.build(_STATS, top=int(config.getoption("querycount_top")))
    if not lines:
        return
    terminalreporter.write_sep("=", "querycount summary")
    for line in lines:
        terminalreporter.write_line(line)


def _write_budgets(terminalreporter: Any) -> None:
    results = [writer.rewrite_file(path, counts) for path, counts in sorted(_OBSERVED.items())]
    terminalreporter.write_sep("=", "querycount: budgets written")
    for line in writer.summarise(results):
        terminalreporter.write_line(line)
