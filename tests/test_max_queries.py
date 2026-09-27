"""The max_queries marker: the plugin's headline feature."""

from __future__ import annotations

import pytest


def test_passes_within_budget(app: pytest.Pytester) -> None:
    app.makepyfile(
        """
        import pytest
        from conftest import list_users_eager

        @pytest.mark.max_queries(2)
        def test_eager(session):
            assert list_users_eager(session) == 10
        """
    )
    app.runpytest_subprocess().assert_outcomes(passed=1)


def test_fails_over_budget(app: pytest.Pytester) -> None:
    app.makepyfile(
        """
        import pytest
        from conftest import list_users_n_plus_one

        @pytest.mark.max_queries(2)
        def test_lazy(session):
            assert list_users_n_plus_one(session) == 10
        """
    )
    result = app.runpytest_subprocess()
    result.assert_outcomes(failed=1)
    # 1 query for the users + 1 per user for their orders.
    result.stdout.fnmatch_lines(["*ran 6 queries, budget is 2 (4 over)*"])


def test_failure_names_the_repeated_query_and_its_caller(app: pytest.Pytester) -> None:
    """The message has to be actionable, not just a number."""
    app.makepyfile(
        """
        import pytest
        from conftest import list_users_n_plus_one

        @pytest.mark.max_queries(1)
        def test_lazy(session):
            list_users_n_plus_one(session)
        """
    )
    result = app.runpytest_subprocess()
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(
        [
            "*Repeated query shapes*",
            "*5x*SELECT*orders*",
            "*conftest.py:*",
        ]
    )


def test_budget_of_zero_is_honoured(app: pytest.Pytester) -> None:
    """A budget of 0 must mean 0, not 'unset'."""
    app.makepyfile(
        """
        import pytest
        from sqlalchemy import select
        from conftest import User

        @pytest.mark.max_queries(0)
        def test_touches_db(session):
            session.scalars(select(User)).all()
        """
    )
    result = app.runpytest_subprocess()
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*ran 1 queries, budget is 0*"])


def test_fixture_setup_is_not_counted(app: pytest.Pytester) -> None:
    """Only the call phase counts, so DDL and seeding never eat the budget."""
    app.makepyfile(
        """
        import pytest

        @pytest.mark.max_queries(0)
        def test_setup_only(session):
            pass
        """
    )
    app.runpytest_subprocess().assert_outcomes(passed=1)


def test_a_real_failure_is_not_masked(app: pytest.Pytester) -> None:
    """When the test itself fails, report that -- not the query count."""
    app.makepyfile(
        """
        import pytest
        from conftest import list_users_n_plus_one

        @pytest.mark.max_queries(1)
        def test_broken(session):
            list_users_n_plus_one(session)
            assert False, "the real problem"
        """
    )
    result = app.runpytest_subprocess()
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*the real problem*"])
    assert "budget is 1" not in result.stdout.str()


def test_global_default_via_cli(app: pytest.Pytester) -> None:
    app.makepyfile(
        """
        from conftest import list_users_n_plus_one

        def test_unmarked(session):
            list_users_n_plus_one(session)
        """
    )
    app.runpytest_subprocess("--querycount-max=6").assert_outcomes(passed=1)
    app.runpytest_subprocess("--querycount-max=5").assert_outcomes(failed=1)


def test_marker_overrides_global_default(app: pytest.Pytester) -> None:
    app.makepyfile(
        """
        import pytest
        from conftest import list_users_n_plus_one

        @pytest.mark.max_queries(6)
        def test_marked(session):
            list_users_n_plus_one(session)
        """
    )
    app.runpytest_subprocess("--querycount-max=1").assert_outcomes(passed=1)


def test_global_default_via_ini(app: pytest.Pytester) -> None:
    app.makeini("[pytest]\nquerycount_max = 5\n")
    app.makepyfile(
        """
        from conftest import list_users_n_plus_one

        def test_unmarked(session):
            list_users_n_plus_one(session)
        """
    )
    app.runpytest_subprocess().assert_outcomes(failed=1)


def test_marker_without_a_number_is_a_usage_error(app: pytest.Pytester) -> None:
    """A typo in the marker must be loud, not silently ignored."""
    app.makepyfile(
        """
        import pytest

        @pytest.mark.max_queries
        def test_no_argument(session):
            pass
        """
    )
    result = app.runpytest_subprocess()
    assert result.ret != 0
    result.stderr.fnmatch_lines(["*max_queries needs a number*"])
