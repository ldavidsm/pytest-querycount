"""Shared machinery for the plugin's own test suite.

Most tests here run pytest inside pytest: ``pytester`` writes a throwaway test
file, runs it in a subprocess, and we assert on the outcome. That is the only
honest way to test a plugin whose job is to make tests fail.
"""

from __future__ import annotations

import contextlib
import os

import pytest

pytest_plugins = ["pytester"]


# A miniature SQLAlchemy app with a deliberate N+1 available in it. Written into
# the pytester directory as conftest.py so every generated test can import it.
APP_CONFTEST = '''
import pytest
from sqlalchemy import ForeignKey, create_engine, select
from sqlalchemy.orm import (
    DeclarativeBase, Mapped, Session, mapped_column, relationship, selectinload,
)
from sqlalchemy.pool import StaticPool


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    orders: Mapped[list["Order"]] = relationship(back_populates="user")


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    total: Mapped[int]
    user: Mapped[User] = relationship(back_populates="orders")


@pytest.fixture
def session():
    """An in-memory database seeded with 5 users of 2 orders each.

    Setup happens in the fixture phase on purpose: the plugin only counts the
    call phase, so this DDL and seeding never land in a budget.
    """
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        for index in range(5):
            user = User(name=f"user{index}")
            user.orders = [Order(total=index * 10), Order(total=index * 20)]
            session.add(user)
        session.commit()
        session.expunge_all()
        yield session


def list_users_n_plus_one(session):
    """1 query for the users, then 1 per user for their orders."""
    total = 0
    for user in session.scalars(select(User)):
        total += len(user.orders)
    return total


def list_users_eager(session):
    """2 queries, whatever the number of users."""
    total = 0
    for user in session.scalars(select(User).options(selectinload(User.orders))):
        total += len(user.orders)
    return total
'''


@pytest.fixture
def app(pytester: pytest.Pytester) -> pytest.Pytester:
    """A pytester directory with the miniature app already in place."""
    pytester.makeconftest(APP_CONFTEST)
    return pytester


# -- PostgreSQL -----------------------------------------------------------
#
# The plan checks need a real PostgreSQL: they read EXPLAIN (FORMAT JSON), and
# no amount of SQLite will stand in for that. Locally these skip when no server
# is reachable; in CI, QUERYCOUNT_REQUIRE_PG=1 turns a skip into a failure so a
# broken service cannot quietly stop testing the feature.

PG_URL = os.environ.get(
    "QUERYCOUNT_TEST_PG_URL",
    "postgresql+psycopg://postgres:querycount@localhost:55432/querycount",
)


def _postgres_reachable() -> str:
    try:
        from sqlalchemy import create_engine
    except ImportError as error:  # pragma: no cover
        return f"SQLAlchemy unavailable: {error}"
    try:
        engine = create_engine(PG_URL, connect_args={"connect_timeout": 3})
        with engine.connect():
            pass
    except Exception as error:
        return f"{type(error).__name__}: {error}"
    finally:
        with contextlib.suppress(Exception):
            engine.dispose()
    return ""


@pytest.fixture(scope="session")
def postgres_url() -> str:
    problem = _postgres_reachable()
    if problem:
        if os.environ.get("QUERYCOUNT_REQUIRE_PG") == "1":
            pytest.fail(f"PostgreSQL is required but unreachable at {PG_URL} -- {problem}")
        pytest.skip(f"no PostgreSQL at {PG_URL} -- {problem}")
    return PG_URL


# A schema with one indexed column and one deliberately unindexed one. Kept
# small on purpose: the whole claim of the seq-scan check is that it works
# regardless of data volume, so the tests must not smuggle in a large table.
PG_CONFTEST = '''
import pytest
from sqlalchemy import Index, String, create_engine, select, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

URL = {url!r}


class Base(DeclarativeBase):
    pass


class Customer(Base):
    __tablename__ = "qc_customers"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(200), index=True)
    city: Mapped[str] = mapped_column(String(200))          # no index, on purpose


@pytest.fixture
def pg():
    engine = create_engine(URL)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        for index in range(8):
            session.add(Customer(email=f"c{{index}}@example.com", city=f"city{{index}}"))
        session.commit()
        session.expunge_all()
        yield session
    Base.metadata.drop_all(engine)
    engine.dispose()


def by_email(session, email="c3@example.com"):
    """Filters an indexed column."""
    return session.scalars(select(Customer).where(Customer.email == email)).all()


def by_city(session, city="city3"):
    """Filters a column with no index -- the bug this check exists to find."""
    return session.scalars(select(Customer).where(Customer.city == city)).all()
'''


@pytest.fixture
def pg_app(pytester: pytest.Pytester, postgres_url: str) -> pytest.Pytester:
    pytester.makeconftest(PG_CONFTEST.format(url=postgres_url))
    return pytester


# -- asyncio --------------------------------------------------------------
#
# SQLAlchemy runs its asyncio support on greenlets, which changes two things
# that matter here: the query is emitted inside a spawned greenlet, and the
# caller's frames live on the parent greenlet's stack. Both are covered below,
# because the target audience for this plugin -- FastAPI, Litestar -- is async
# almost by default.

ASYNC_CONFTEST = '''
import pytest
import pytest_asyncio
from sqlalchemy import ForeignKey, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.pool import StaticPool


class Base(DeclarativeBase):
    pass


class Author(Base):
    __tablename__ = "authors"
    id: Mapped[int] = mapped_column(primary_key=True)
    books: Mapped[list["Book"]] = relationship()


class Book(Base):
    __tablename__ = "books"
    id: Mapped[int] = mapped_column(primary_key=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("authors.id"))


@pytest_asyncio.fixture
async def adb():
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSession(engine) as session:
        for _ in range(5):
            session.add(Author(books=[Book(), Book()]))
        await session.commit()
        session.expunge_all()
        yield session
    await engine.dispose()


async def list_authors_n_plus_one(session):
    """1 query for the authors, then 1 per author. Awaited lazy loads need
    AsyncSession.refresh or an explicit select, so the loop is explicit."""
    total = 0
    for author in (await session.scalars(select(Author))).all():
        rows = (await session.scalars(
            select(Book).where(Book.author_id == author.id)
        )).all()
        total += len(rows)
    return total


async def list_authors_eager(session):
    from sqlalchemy.orm import selectinload

    stmt = select(Author).options(selectinload(Author.books))
    authors = (await session.scalars(stmt)).all()
    return sum(len(a.books) for a in authors)
'''


@pytest.fixture
def async_app(pytester: pytest.Pytester) -> pytest.Pytester:
    pytester.makeconftest(ASYNC_CONFTEST)
    pytester.makeini("[pytest]\nasyncio_mode = auto\n")
    return pytester


# The plan check runs an EXPLAIN on a raw DBAPI cursor. Under asyncio that
# cursor is a greenlet-backed adapter, so the synchronous-looking execute has to
# survive being called from inside the spawned greenlet. Worth its own fixture
# because "it works synchronously" says nothing about that.
ASYNC_PG_CONFTEST = """
import pytest
import pytest_asyncio
from sqlalchemy import String, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

URL = {url!r}


class Base(DeclarativeBase):
    pass


class Customer(Base):
    __tablename__ = "qc_async_customers"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(200), index=True)
    city: Mapped[str] = mapped_column(String(200))          # no index, on purpose


@pytest_asyncio.fixture
async def apg():
    engine = create_async_engine(URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSession(engine) as session:
        for index in range(8):
            session.add(Customer(email=f"a{{index}}@example.com", city=f"city{{index}}"))
        await session.commit()
        session.expunge_all()
        yield session
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


async def by_email(session, email="a3@example.com"):
    return (await session.scalars(select(Customer).where(Customer.email == email))).all()


async def by_city(session, city="city3"):
    return (await session.scalars(select(Customer).where(Customer.city == city))).all()
"""


@pytest.fixture
def async_pg_app(pytester: pytest.Pytester, postgres_url: str) -> pytest.Pytester:
    pytester.makeconftest(ASYNC_PG_CONFTEST.format(url=postgres_url))
    pytester.makeini("[pytest]\nasyncio_mode = auto\n")
    return pytester
