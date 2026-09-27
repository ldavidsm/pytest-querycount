"""SQLAlchemy 2.x instrumentation.

We listen on the ``Engine`` *class* rather than on individual engines, so every
engine the suite builds is covered without the project having to register
anything. Async engines are covered too: ``AsyncEngine`` wraps a sync ``Engine``
and the cursor events fire on that.
"""

from __future__ import annotations

import contextlib
import time
from typing import Any

from pytest_querycount import backends, explain
from pytest_querycount.normalize import fingerprint, statement_kind
from pytest_querycount.recorder import caller_location
from pytest_querycount.records import QueryRecord

NAME = "sqlalchemy"

# Only PostgreSQL exposes a machine-readable plan we can reason about this way.
_EXPLAINABLE_DIALECTS = {"postgresql"}
_SAVEPOINT = "pytest_querycount_explain"

_installed = False

# Fallback timing stack, used when SQLAlchemy hands us no execution context
# (raw ``exec_driver_sql`` calls, mainly). Durations are informational, so a
# plain stack is enough and we never let it grow without bound.
_fallback_starts: list[float] = []
_CONTEXT_ATTR = "_querycount_start"
_SCANS_ATTR = "_querycount_scans"


def install() -> bool:
    """Attach the listeners. Returns False when SQLAlchemy is not available."""
    global _installed
    if _installed:
        return True
    try:
        from sqlalchemy import event
        from sqlalchemy.engine import Engine
    except ImportError:
        return False

    event.listen(Engine, "before_cursor_execute", _before_cursor_execute)
    event.listen(Engine, "after_cursor_execute", _after_cursor_execute)
    _installed = True
    return True


def instrument(engine: Any) -> None:
    """Attach the listeners to one engine explicitly.

    Only needed for an engine built before the plugin loaded, which in a pytest
    run should not happen. Kept because "should not happen" is not "cannot".
    """
    from sqlalchemy import event

    target = getattr(engine, "sync_engine", engine)
    event.listen(target, "before_cursor_execute", _before_cursor_execute)
    event.listen(target, "after_cursor_execute", _after_cursor_execute)


def _before_cursor_execute(
    conn: Any,
    cursor: Any,
    statement: str,
    parameters: Any,
    context: Any,
    executemany: bool,
) -> None:
    recorders = backends.active()
    if not recorders:
        return

    scans: tuple[explain.SeqScan, ...] | None = None
    if any(recorder.explain for recorder in recorders) and not executemany:
        scans = _plan_seq_scans(conn, statement, parameters)

    start = time.perf_counter()
    if context is not None:
        if scans is not None:
            # Only reached when EXPLAIN is on, so the context manager's cost here
            # does not matter the way it does on the per-query timing path.
            with contextlib.suppress(AttributeError, TypeError):
                setattr(context, _SCANS_ATTR, scans)
        try:
            setattr(context, _CONTEXT_ATTR, start)
            return
        except (AttributeError, TypeError):  # pragma: no cover - slotted context
            pass
    _fallback_starts.append(start)


def _after_cursor_execute(
    conn: Any,
    cursor: Any,
    statement: str,
    parameters: Any,
    context: Any,
    executemany: bool,
) -> None:
    if not backends.active():
        return

    start = _take_start(context)
    duration = time.perf_counter() - start if start is not None else 0.0
    scans = _take_scans(context)

    backends.emit(
        QueryRecord(
            sql=statement,
            fingerprint=fingerprint(statement),
            kind=statement_kind(statement),
            duration=duration,
            executemany=bool(executemany),
            rowcount=_rowcount(cursor),
            seq_scans=scans or (),
            explained=scans is not None,
            location=caller_location(),
        )
    )


def _take_scans(context: Any) -> tuple[explain.SeqScan, ...] | None:
    if context is None:
        return None
    scans = getattr(context, _SCANS_ATTR, None)
    if scans is None:
        return None
    try:  # noqa: SIM105
        delattr(context, _SCANS_ATTR)
    except (AttributeError, TypeError):  # pragma: no cover
        pass
    return scans  # type: ignore[no-any-return]


def _plan_seq_scans(
    conn: Any,
    statement: str,
    parameters: Any,
) -> tuple[explain.SeqScan, ...] | None:
    """Ask PostgreSQL for a plan with sequential scans penalised.

    Returns the filtered sequential scans the planner kept anyway, or None when
    no plan could be obtained -- a different answer from "found nothing", and
    the caller must not conflate them.

    Runs on a raw DBAPI cursor rather than through the engine, which keeps the
    EXPLAIN out of SQLAlchemy's event stream: it is neither counted as one of
    the test's queries nor able to recurse into this function.
    """
    if statement_kind(statement) != "select":
        # Explaining a write is possible but buys little and risks more.
        return None
    if getattr(conn.dialect, "name", None) not in _EXPLAINABLE_DIALECTS:
        return None

    try:
        cursor = conn.connection.cursor()
    except Exception:  # pragma: no cover - pool handed us nothing usable
        return None

    # The savepoint does double duty: it scopes the enable_seqscan change so we
    # cannot leak it into the test, and it absorbs a failed EXPLAIN, which would
    # otherwise abort the transaction the test is running in.
    guarded = bool(conn.in_transaction())
    try:
        if guarded:
            cursor.execute(f"SAVEPOINT {_SAVEPOINT}")
        cursor.execute("SET enable_seqscan = off")
        cursor.execute("EXPLAIN (FORMAT JSON) " + statement, parameters or None)
        row = cursor.fetchone()
        return explain.parse(row[0]) if row else ()
    except Exception:
        return None
    finally:
        try:
            if guarded:
                cursor.execute(f"ROLLBACK TO SAVEPOINT {_SAVEPOINT}")
            else:
                cursor.execute("RESET enable_seqscan")
        except Exception:  # pragma: no cover - connection already unusable
            pass
        try:  # noqa: SIM105
            cursor.close()
        except Exception:  # pragma: no cover
            pass


def _take_start(context: Any) -> float | None:
    if context is not None:
        start = getattr(context, _CONTEXT_ATTR, None)
        if start is not None:
            # try/except rather than contextlib.suppress: this runs once per
            # query, and a context manager here is measurable overhead in a
            # library whose whole job is to sit in that path.
            try:  # noqa: SIM105
                delattr(context, _CONTEXT_ATTR)
            except (AttributeError, TypeError):  # pragma: no cover
                pass
            return float(start)
    if _fallback_starts:
        return _fallback_starts.pop()
    return None


def _rowcount(cursor: Any) -> int | None:
    try:
        count = cursor.rowcount
    except Exception:  # pragma: no cover - drivers may refuse after DDL
        return None
    return count if isinstance(count, int) and count >= 0 else None
