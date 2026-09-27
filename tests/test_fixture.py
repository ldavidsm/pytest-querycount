"""The querycount fixture, for when a marker's whole-test scope is too coarse."""

from __future__ import annotations

import pytest


def test_records_without_asserting(app: pytest.Pytester) -> None:
    """Called bare, it observes -- which is how you discover a budget."""
    app.makepyfile(
        """
        from conftest import list_users_n_plus_one

        def test_observe(session, querycount):
            with querycount() as queries:
                list_users_n_plus_one(session)

            assert queries.count == 6
            assert len(queries.duplicates()) == 1
            assert queries.duplicates()[0].count == 5
            assert queries.total_duration > 0
        """
    )
    app.runpytest_subprocess().assert_outcomes(passed=1)


def test_enforces_its_own_budget(app: pytest.Pytester) -> None:
    app.makepyfile(
        """
        from conftest import list_users_n_plus_one

        def test_block_budget(session, querycount):
            with querycount(max_queries=2):
                list_users_n_plus_one(session)
        """
    )
    result = app.runpytest_subprocess()
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*This querycount block ran 6 queries, budget is 2*"])


def test_scopes_to_the_block_only(app: pytest.Pytester) -> None:
    """Queries outside the with-block must not count against it."""
    app.makepyfile(
        """
        from conftest import list_users_eager, list_users_n_plus_one

        def test_scoped(session, querycount):
            list_users_n_plus_one(session)          # 6 queries, not counted
            with querycount(max_queries=2) as queries:
                list_users_eager(session)           # 2 queries, counted
            assert queries.count == 2
        """
    )
    app.runpytest_subprocess().assert_outcomes(passed=1)


def test_nests_inside_a_marker(app: pytest.Pytester) -> None:
    """The per-test recorder and the block recorder both see the same queries."""
    app.makepyfile(
        """
        import pytest
        from conftest import list_users_eager

        @pytest.mark.max_queries(4)
        def test_both(session, querycount):
            with querycount(max_queries=2) as queries:
                list_users_eager(session)
            with querycount(max_queries=2) as more:
                list_users_eager(session)
            assert queries.count == 2 and more.count == 2
        """
    )
    app.runpytest_subprocess().assert_outcomes(passed=1)


def test_block_n_plus_one(app: pytest.Pytester) -> None:
    app.makepyfile(
        """
        from conftest import list_users_n_plus_one

        def test_block_loop(session, querycount):
            with querycount(no_n_plus_one=True):
                list_users_n_plus_one(session)
        """
    )
    result = app.runpytest_subprocess()
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*This querycount block repeated the same query 5 times*"])


def test_an_error_in_the_block_wins(app: pytest.Pytester) -> None:
    """Do not replace the user's exception with a query complaint."""
    app.makepyfile(
        """
        from conftest import list_users_n_plus_one

        def test_raises(session, querycount):
            with querycount(max_queries=1):
                list_users_n_plus_one(session)
                raise ValueError("the real problem")
        """
    )
    result = app.runpytest_subprocess()
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*ValueError: the real problem*"])
    assert "budget is 1" not in result.stdout.str()


def test_report_lists_the_queries(app: pytest.Pytester) -> None:
    app.makepyfile(
        """
        from conftest import list_users_eager

        def test_report(session, querycount):
            with querycount() as queries:
                list_users_eager(session)
            text = queries.report()
            assert "2 queries" in text
            assert "SELECT" in text
            assert "conftest.py" in text
        """
    )
    app.runpytest_subprocess().assert_outcomes(passed=1)


def test_report_collapses_repeated_shapes(app: pytest.Pytester) -> None:
    """Twelve copies of one SELECT must print as one line, not twelve.

    The listing is the evidence behind a failure, and evidence nobody can read
    is not evidence.
    """
    app.makepyfile(
        """
        from conftest import list_users_n_plus_one

        def test_collapse(session, querycount):
            with querycount() as queries:
                list_users_n_plus_one(session)

            collapsed = queries.report()
            expanded = queries.report(collapse=False)

            assert queries.count == 6
            assert "5x" in collapsed
            assert "2-6." in collapsed
            assert "5x" not in expanded

            # One line per query plus one origin line each, versus far fewer.
            assert len(collapsed.splitlines()) < len(expanded.splitlines())
        """
    )
    app.runpytest_subprocess().assert_outcomes(passed=1)
