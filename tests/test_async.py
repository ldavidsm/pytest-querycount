"""SQLAlchemy's asyncio support, which is where the caller attribution nearly
silently stopped working.

Under asyncio, SQLAlchemy runs its synchronous internals inside a greenlet
spawned per operation. That greenlet's stack begins at SQLAlchemy's own entry
point, so following ``f_back`` reaches only library frames and never the test's
own lines -- the budget still fired, but every failure lost the one piece of
information that makes it actionable. These tests hold the fix in place.
"""

from __future__ import annotations

import pytest


def test_async_engine_is_instrumented(async_app: pytest.Pytester) -> None:
    async_app.makepyfile(
        """
        import pytest
        from conftest import list_authors_eager

        @pytest.mark.max_queries(2)
        async def test_eager(adb):
            assert await list_authors_eager(adb) == 10
        """
    )
    async_app.runpytest_subprocess().assert_outcomes(passed=1)


def test_async_budget_fires(async_app: pytest.Pytester) -> None:
    async_app.makepyfile(
        """
        import pytest
        from conftest import list_authors_n_plus_one

        @pytest.mark.max_queries(2)
        async def test_lazy(adb):
            assert await list_authors_n_plus_one(adb) == 10
        """
    )
    result = async_app.runpytest_subprocess()
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*ran 6 queries, budget is 2*"])


def test_async_failures_name_the_caller(async_app: pytest.Pytester) -> None:
    """The regression this module exists for.

    Before the greenlet-crossing walk, the failure carried no origin at all: no
    "from" line, because every reachable frame belonged to SQLAlchemy.
    """
    async_app.makepyfile(
        """
        import pytest
        from conftest import list_authors_n_plus_one

        @pytest.mark.max_queries(1)
        async def test_lazy(adb):
            await list_authors_n_plus_one(adb)
        """
    )
    result = async_app.runpytest_subprocess()
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*from conftest.py:*"])


def test_async_n_plus_one_detection(async_app: pytest.Pytester) -> None:
    async_app.makepyfile(
        """
        import pytest
        from conftest import list_authors_n_plus_one

        @pytest.mark.no_n_plus_one
        async def test_lazy(adb):
            await list_authors_n_plus_one(adb)
        """
    )
    result = async_app.runpytest_subprocess()
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*repeated the same query 5 times*", "*from conftest.py:*"])


def test_async_fixture_records_locations(async_app: pytest.Pytester) -> None:
    """Every record carries an origin, not just the ones in a failure message."""
    async_app.makepyfile(
        """
        from conftest import list_authors_eager

        async def test_locations(adb, querycount):
            with querycount() as queries:
                await list_authors_eager(adb)

            assert queries.count == 2
            assert all(record.location for record in queries.records), (
                "async records lost their caller attribution:\\n"
                + "\\n".join(repr(r.location) for r in queries.records)
            )
            assert "conftest.py" in queries.report()
        """
    )
    async_app.runpytest_subprocess().assert_outcomes(passed=1)


# -- the plan check under asyncio -----------------------------------------


def test_async_seq_scan_detection(async_pg_app: pytest.Pytester) -> None:
    """The EXPLAIN goes out on a raw DBAPI cursor. Under asyncio that cursor is
    a greenlet-backed adapter, so this is not implied by the sync case."""
    async_pg_app.makepyfile(
        """
        import pytest
        from conftest import by_city

        @pytest.mark.no_seq_scan
        async def test_unindexed(apg):
            assert len(await by_city(apg)) == 1
        """
    )
    result = async_pg_app.runpytest_subprocess()
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(
        [
            "*no index could serve*",
            '*Seq Scan on "qc_async_customers"*city*',
            "*CREATE INDEX ON qc_async_customers (city);*",
        ]
    )


def test_async_indexed_column_passes(async_pg_app: pytest.Pytester) -> None:
    async_pg_app.makepyfile(
        """
        import pytest
        from conftest import by_email

        @pytest.mark.no_seq_scan
        async def test_indexed(apg):
            assert len(await by_email(apg)) == 1
        """
    )
    async_pg_app.runpytest_subprocess().assert_outcomes(passed=1)


def test_async_explain_leaves_no_trace(async_pg_app: pytest.Pytester) -> None:
    """The savepoint has to restore the session setting and keep the
    transaction usable, inside a greenlet just as outside one."""
    async_pg_app.makepyfile(
        """
        import pytest
        from sqlalchemy import text
        from conftest import Customer, by_email

        async def test_undisturbed(apg, querycount):
            with querycount(no_seq_scan=True) as queries:
                await by_email(apg)
                await by_email(apg)

            assert queries.count == 2
            assert queries.explained_count == 2

            setting = (await apg.execute(text("SHOW enable_seqscan"))).scalar()
            assert setting == "on", setting

            apg.add(Customer(email="new@example.com", city="nowhere"))
            await apg.flush()
        """
    )
    async_pg_app.runpytest_subprocess().assert_outcomes(passed=1)
