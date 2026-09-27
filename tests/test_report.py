"""The end-of-session summary table."""

from __future__ import annotations

import pytest

from pytest_querycount import report


def test_off_by_default(app: pytest.Pytester) -> None:
    app.makepyfile(
        """
        from conftest import list_users_eager

        def test_quiet(session):
            list_users_eager(session)
        """
    )
    result = app.runpytest_subprocess()
    result.assert_outcomes(passed=1)
    assert "querycount summary" not in result.stdout.str()


def test_lists_tests_worst_first(app: pytest.Pytester) -> None:
    app.makepyfile(
        """
        from conftest import list_users_eager, list_users_n_plus_one

        def test_heavy(session):
            list_users_n_plus_one(session)

        def test_light(session):
            list_users_eager(session)
        """
    )
    result = app.runpytest_subprocess("--querycount-report")
    result.assert_outcomes(passed=2)
    result.stdout.fnmatch_lines(
        [
            "*querycount summary*",
            "*queries*time*dupes*test*",
            "*6*test_heavy*",
            "*2*test_light*",
            "*8 queries in*across 2 tests*",
        ]
    )


def test_flags_repeated_shapes(app: pytest.Pytester) -> None:
    """The ! is the whole point: it tells you where to add a marker."""
    app.makepyfile(
        """
        from conftest import list_users_n_plus_one

        def test_heavy(session):
            list_users_n_plus_one(session)
        """
    )
    result = app.runpytest_subprocess("--querycount-report")
    result.stdout.fnmatch_lines(["*5!*test_heavy*", "*! marks a repeated query shape*"])


def test_top_limits_the_table(app: pytest.Pytester) -> None:
    app.makepyfile(
        """
        import pytest
        from conftest import list_users_eager

        @pytest.mark.parametrize("n", range(4))
        def test_many(session, n):
            list_users_eager(session)
        """
    )
    result = app.runpytest_subprocess("--querycount-report", "--querycount-top=2")
    result.assert_outcomes(passed=4)
    result.stdout.fnmatch_lines(["*2 more not shown, raise --querycount-top*"])


def test_tests_without_queries_are_omitted(app: pytest.Pytester) -> None:
    app.makepyfile(
        """
        def test_no_db():
            assert True
        """
    )
    result = app.runpytest_subprocess("--querycount-report")
    result.assert_outcomes(passed=1)
    assert "querycount summary" not in result.stdout.str()


def test_build_returns_nothing_when_empty() -> None:
    assert report.build({}) == []
    assert (
        report.build({"a": report.TestStats("a", count=0, duration=0.0, worst_duplicate=0)}) == []
    )
