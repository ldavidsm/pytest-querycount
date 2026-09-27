"""Instrumentation: turning driver activity into :class:`QueryRecord` objects.

A backend is responsible for one database library. It reports queries to every
recorder currently on the active stack, which is what makes nesting work: the
per-test recorder and a ``querycount(...)`` block inside that test both see the
same queries.
"""

from __future__ import annotations

import contextlib
from collections.abc import Sequence

from pytest_querycount.recorder import Recorder
from pytest_querycount.records import QueryRecord

_active: list[Recorder] = []
_installed: list[str] = []


def push(recorder: Recorder) -> None:
    """Start sending queries to ``recorder``."""
    _active.append(recorder)


def pop(recorder: Recorder) -> None:
    """Stop sending queries to ``recorder``."""
    with contextlib.suppress(ValueError):  # a pop without a push is not fatal
        _active.remove(recorder)


def active() -> Sequence[Recorder]:
    return _active


def emit(record: QueryRecord) -> None:
    """Hand ``record`` to every listening recorder."""
    for recorder in _active:
        recorder.add(record)


def installed() -> Sequence[str]:
    """Names of the backends that were successfully instrumented."""
    return _installed


def install_all() -> Sequence[str]:
    """Instrument every library we support and that is importable.

    Missing libraries are not an error -- most projects use one ORM, not all of
    them. But *no* backend at all is an error the moment a check runs, since a
    query budget that cannot see queries would pass silently forever.
    """
    from pytest_querycount.backends import sqlalchemy as sqlalchemy_backend

    for backend in (sqlalchemy_backend,):
        if backend.install() and backend.NAME not in _installed:
            _installed.append(backend.NAME)
    return _installed
