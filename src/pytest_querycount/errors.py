"""Failures raised by the plugin.

All of them subclass ``AssertionError`` so pytest renders them as ordinary test
failures rather than errors.
"""

from __future__ import annotations


class QueryCountError(AssertionError):
    """Base class for every check failure."""


class TooManyQueriesError(QueryCountError):
    """A test ran more queries than its budget allowed."""


class DuplicateQueryError(QueryCountError):
    """The same query shape was executed repeatedly -- the N+1 signature."""


class NoBackendError(QueryCountError):
    """A check was requested but nothing is instrumented, so it would be a no-op.

    This is deliberately loud: a query budget that silently always passes is
    worse than no budget at all.
    """


class SeqScanError(QueryCountError):
    """A query scanned a table sequentially because no index could serve it."""


class ExplainUnavailableError(QueryCountError):
    """A plan check ran where no plan could be obtained.

    Same reasoning as :class:`NoBackendError`: a check that cannot fail is not a
    check, so we say so rather than pass.
    """
