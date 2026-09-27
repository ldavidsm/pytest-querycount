"""Fail your tests when they run too many SQL queries."""

from __future__ import annotations

__version__ = "0.4.0"

from pytest_querycount.errors import (
    DuplicateQueryError,
    ExplainUnavailableError,
    NoBackendError,
    SeqScanError,
    TooManyQueriesError,
)
from pytest_querycount.explain import SeqScan
from pytest_querycount.normalize import fingerprint
from pytest_querycount.recorder import Recorder
from pytest_querycount.records import Duplicate, QueryRecord

__all__ = [
    "Duplicate",
    "DuplicateQueryError",
    "ExplainUnavailableError",
    "NoBackendError",
    "QueryRecord",
    "Recorder",
    "SeqScan",
    "SeqScanError",
    "TooManyQueriesError",
    "__version__",
    "fingerprint",
]
