"""The no_n_plus_one marker: a budget you do not have to pick a number for."""

from __future__ import annotations

import pytest


def test_detects_the_lazy_load(app: pytest.Pytester) -> None:
    app.makepyfile(
        """
        import pytest
        from conftest import list_users_n_plus_one

        @pytest.mark.no_n_plus_one
        def test_lazy(session):
            list_users_n_plus_one(session)
        """
    )
    result = app.runpytest_subprocess()
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(
        [
            "*repeated the same query 5 times*",
            "*This is the N+1 pattern*selectinload*",
            "*5x*SELECT orders*",
            "*from conftest.py:*",
        ]
    )


def test_eager_loading_passes(app: pytest.Pytester) -> None:
    """The fix has to actually satisfy the check, or the check is useless."""
    app.makepyfile(
        """
        import pytest
        from conftest import list_users_eager

        @pytest.mark.no_n_plus_one
        def test_eager(session):
            assert list_users_eager(session) == 10
        """
    )
    app.runpytest_subprocess().assert_outcomes(passed=1)


def test_threshold_tolerates_a_few_repeats(app: pytest.Pytester) -> None:
    app.makepyfile(
        """
        import pytest
        from conftest import list_users_n_plus_one

        @pytest.mark.no_n_plus_one(threshold=6)
        def test_five_repeats_allowed(session):
            list_users_n_plus_one(session)

        @pytest.mark.no_n_plus_one(threshold=5)
        def test_five_repeats_rejected(session):
            list_users_n_plus_one(session)
        """
    )
    app.runpytest_subprocess().assert_outcomes(passed=1, failed=1)


def test_writes_are_ignored_by_default(app: pytest.Pytester) -> None:
    """Repeated INSERTs are a different problem; the default is about SELECTs."""
    app.makepyfile(
        """
        import pytest
        from conftest import Order

        @pytest.mark.no_n_plus_one
        def test_many_inserts(session):
            for index in range(5):
                session.add(Order(user_id=1, total=index))
            session.flush()
        """
    )
    app.runpytest_subprocess().assert_outcomes(passed=1)


def test_writes_can_be_included(app: pytest.Pytester) -> None:
    app.makepyfile(
        """
        import pytest
        from conftest import Order

        @pytest.mark.no_n_plus_one(kinds=("insert",))
        def test_many_inserts(session):
            for index in range(5):
                session.add(Order(user_id=1, total=index))
                session.flush()
        """
    )
    app.runpytest_subprocess().assert_outcomes(failed=1)


def test_kinds_none_considers_everything(app: pytest.Pytester) -> None:
    app.makepyfile(
        """
        import pytest
        from conftest import Order

        @pytest.mark.no_n_plus_one(kinds=None)
        def test_many_inserts(session):
            for index in range(5):
                session.add(Order(user_id=1, total=index))
                session.flush()
        """
    )
    app.runpytest_subprocess().assert_outcomes(failed=1)


def test_combines_with_a_budget(app: pytest.Pytester) -> None:
    """Both markers on one test: the budget is checked first, being cheaper."""
    app.makepyfile(
        """
        import pytest
        from conftest import list_users_n_plus_one

        @pytest.mark.max_queries(10)
        @pytest.mark.no_n_plus_one
        def test_within_budget_but_looping(session):
            list_users_n_plus_one(session)
        """
    )
    result = app.runpytest_subprocess()
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*repeated the same query 5 times*"])
