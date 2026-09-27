"""A budget that cannot see queries must shout, not pass.

This is the failure mode that would quietly undermine every other feature: if
nothing is instrumented, every count is zero and every budget is satisfied. So
it is an error, and it is tested.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from pytest_querycount import backends, checks
from pytest_querycount.checks import Budget
from pytest_querycount.errors import NoBackendError
from pytest_querycount.recorder import Recorder


@pytest.fixture
def without_backends(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(backends, "_installed", [])
    yield


def test_budget_without_a_backend_raises(without_backends: None) -> None:
    with pytest.raises(NoBackendError, match="nothing to watch"):
        checks.enforce(Budget(max_queries=5), Recorder())


def test_n_plus_one_without_a_backend_raises(without_backends: None) -> None:
    with pytest.raises(NoBackendError):
        checks.enforce(Budget(no_n_plus_one=True), Recorder())


def test_an_empty_budget_stays_quiet(without_backends: None) -> None:
    """No marker, no default: nothing to check, so nothing to complain about."""
    checks.enforce(Budget(), Recorder())


def test_sqlalchemy_is_detected_normally() -> None:
    assert "sqlalchemy" in backends.installed()
