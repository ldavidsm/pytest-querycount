"""--querycount-write-budgets, which edits the user's test files.

Held to a higher standard than the rest of the plugin for that reason: the unit
tests below cover the shapes real test files come in -- classes, decorators,
async definitions, multi-line signatures, missing imports -- because getting
"the line above the def" wrong here corrupts somebody's source.
"""

from __future__ import annotations

import ast

import pytest

from pytest_querycount import writer


def _parses(source: str) -> ast.Module:
    """Guard rail: whatever we emit has to still be Python."""
    return ast.parse(source)


def test_adds_a_marker_above_the_def() -> None:
    source = "import pytest\n\n\ndef test_thing(db):\n    pass\n"
    updated, result = writer.rewrite(source, {"test_thing": 4})
    assert result.written == ["test_thing"]
    assert "@pytest.mark.max_queries(4)\ndef test_thing(db):" in updated
    _parses(updated)


def test_adds_the_pytest_import_when_missing() -> None:
    source = "def test_thing(db):\n    pass\n"
    updated, result = writer.rewrite(source, {"test_thing": 2})
    assert result.written == ["test_thing"]
    assert updated.startswith("import pytest\n")
    _parses(updated)


def test_import_goes_before_existing_imports_not_after() -> None:
    """Appending would leave `import pytest` below a local import, which every
    import sorter then wants to move -- so the file would come back from the
    tool already failing the project's own lint."""
    source = '"""Doc."""\n\nfrom conftest import thing\n\n\ndef test_x():\n    pass\n'
    updated, result = writer.rewrite(source, {"test_x": 1})
    lines = updated.splitlines()
    assert lines[0] == '"""Doc."""'
    assert lines.index("import pytest") < lines.index("from conftest import thing")
    assert result.import_added
    _parses(updated)


def test_future_import_stays_first() -> None:
    """`from __future__ import ...` has to remain the first statement."""
    source = (
        "from __future__ import annotations\n\nfrom conftest import thing\n\n\n"
        "def test_x():\n    pass\n"
    )
    updated, _ = writer.rewrite(source, {"test_x": 1})
    body = [line for line in updated.splitlines() if line.strip()]
    assert body[0] == "from __future__ import annotations"
    assert body[1] == "import pytest"
    _parses(updated)


def test_no_import_note_when_pytest_is_already_imported() -> None:
    source = "import pytest\n\n\ndef test_x():\n    pass\n"
    _, result = writer.rewrite(source, {"test_x": 1})
    assert result.import_added is False


def test_leaves_an_existing_budget_alone() -> None:
    source = "import pytest\n\n\n@pytest.mark.max_queries(9)\ndef test_thing():\n    pass\n"
    updated, result = writer.rewrite(source, {"test_thing": 3})
    assert result.written == []
    assert result.skipped == ["test_thing"]
    assert updated == source


def test_marker_sits_below_other_decorators() -> None:
    source = (
        "import pytest\n\n\n"
        '@pytest.mark.parametrize("n", [1, 2])\n'
        "@pytest.mark.no_n_plus_one\n"
        "def test_thing(n, db):\n"
        "    pass\n"
    )
    updated, result = writer.rewrite(source, {"test_thing": 5})
    assert result.written == ["test_thing"]
    assert "@pytest.mark.no_n_plus_one\n@pytest.mark.max_queries(5)\ndef test_thing" in updated
    _parses(updated)


def test_indents_to_match_a_method() -> None:
    source = "import pytest\n\n\nclass TestThing:\n    def test_inner(self, db):\n        pass\n"
    updated, result = writer.rewrite(source, {"TestThing.test_inner": 6})
    assert result.written == ["TestThing.test_inner"]
    assert "    @pytest.mark.max_queries(6)\n    def test_inner" in updated
    _parses(updated)


def test_same_method_name_in_two_classes_is_not_confused() -> None:
    """Matching on bare names would budget the wrong test here."""
    source = (
        "import pytest\n\n\n"
        "class TestA:\n    def test_create(self):\n        pass\n\n\n"
        "class TestB:\n    def test_create(self):\n        pass\n"
    )
    updated, result = writer.rewrite(source, {"TestB.test_create": 7})
    assert result.written == ["TestB.test_create"]
    assert updated.count("max_queries") == 1
    body = updated[updated.index("class TestB") :]
    assert "@pytest.mark.max_queries(7)" in body
    _parses(updated)


def test_handles_async_definitions() -> None:
    source = "import pytest\n\n\nasync def test_thing(adb):\n    pass\n"
    updated, result = writer.rewrite(source, {"test_thing": 3})
    assert result.written == ["test_thing"]
    assert "@pytest.mark.max_queries(3)\nasync def test_thing" in updated
    _parses(updated)


def test_handles_a_multiline_signature() -> None:
    """The marker must land above `def`, not inside the argument list."""
    source = "import pytest\n\n\ndef test_thing(\n    db,\n    client,\n):\n    pass\n"
    updated, result = writer.rewrite(source, {"test_thing": 2})
    assert result.written == ["test_thing"]
    assert "@pytest.mark.max_queries(2)\ndef test_thing(" in updated
    _parses(updated)


def test_several_functions_keep_their_own_numbers() -> None:
    """Inserting bottom-up is what keeps earlier edits from shifting later ones."""
    source = (
        "import pytest\n\n\n"
        "def test_a():\n    pass\n\n\n"
        "def test_b():\n    pass\n\n\n"
        "def test_c():\n    pass\n"
    )
    updated, result = writer.rewrite(source, {"test_a": 1, "test_b": 2, "test_c": 3})
    assert result.written == ["test_a", "test_b", "test_c"]
    for name, count in (("test_a", 1), ("test_b", 2), ("test_c", 3)):
        assert f"@pytest.mark.max_queries({count})\ndef {name}" in updated
    _parses(updated)


def test_reports_a_function_it_cannot_find() -> None:
    source = "def test_thing():\n    pass\n"
    updated, result = writer.rewrite(source, {"test_gone": 1})
    assert result.missing == ["test_gone"]
    assert updated == source


def test_refuses_to_touch_a_file_it_cannot_parse() -> None:
    source = "def test_thing(:\n    this is not python\n"
    updated, result = writer.rewrite(source, {"test_thing": 1})
    assert result.error is not None
    assert updated == source


def test_preserves_windows_line_endings() -> None:
    source = "import pytest\r\n\r\n\r\ndef test_thing():\r\n    pass\r\n"
    updated, result = writer.rewrite(source, {"test_thing": 1})
    assert result.written == ["test_thing"]
    assert "\r\n@pytest.mark.max_queries(1)\r\ndef test_thing" in updated
    # No bare LF smuggled in alongside the CRLFs.
    assert updated.count("\n") == updated.count("\r\n")


# -- end to end -----------------------------------------------------------


def test_writes_budgets_into_a_real_suite(app: pytest.Pytester) -> None:
    app.makepyfile(
        test_suite="""
        from conftest import list_users_eager, list_users_n_plus_one

        def test_light(session):
            list_users_eager(session)

        def test_heavy(session):
            list_users_n_plus_one(session)

        def test_no_db():
            assert True
        """
    )
    result = app.runpytest_subprocess("--querycount-write-budgets")
    result.assert_outcomes(passed=3)
    result.stdout.fnmatch_lines(["*budgets written*", "*2 marker(s) added*"])

    written = app.path.joinpath("test_suite.py").read_text()
    assert "@pytest.mark.max_queries(2)\ndef test_light" in written
    assert "@pytest.mark.max_queries(6)\ndef test_heavy" in written
    # A test that touched no database gets no budget.
    assert written.count("max_queries") == 2
    ast.parse(written)

    # And the numbers it wrote must be the ones that make the suite pass.
    app.runpytest_subprocess().assert_outcomes(passed=3)


def test_writing_does_not_enforce(app: pytest.Pytester) -> None:
    """Enforcing a budget while deciding what it should be is contradictory."""
    app.makepyfile(
        test_over_budget="""
        import pytest
        from conftest import list_users_n_plus_one

        @pytest.mark.max_queries(1)
        def test_already_failing(session):
            list_users_n_plus_one(session)
        """
    )
    # Fails normally...
    app.runpytest_subprocess().assert_outcomes(failed=1)
    # ...but passes while budgets are being written, and keeps its own marker.
    result = app.runpytest_subprocess("--querycount-write-budgets")
    result.assert_outcomes(passed=1)
    assert "max_queries(1)" in app.path.joinpath("test_over_budget.py").read_text()


def test_a_failing_test_gets_no_budget(app: pytest.Pytester) -> None:
    """Its query count is whatever it happened to reach before blowing up."""
    app.makepyfile(
        test_broken="""
        from conftest import list_users_n_plus_one

        def test_broken(session):
            list_users_n_plus_one(session)
            assert False
        """
    )
    app.runpytest_subprocess("--querycount-write-budgets").assert_outcomes(failed=1)
    assert "max_queries" not in app.path.joinpath("test_broken.py").read_text()


def test_parametrised_tests_get_the_widest_count(app: pytest.Pytester) -> None:
    app.makepyfile(
        test_param="""
        import pytest
        from sqlalchemy import select
        from conftest import User

        @pytest.mark.parametrize("how_many", [1, 3])
        def test_varies(session, how_many):
            for _ in range(how_many):
                session.scalars(select(User)).all()
        """
    )
    app.runpytest_subprocess("--querycount-write-budgets").assert_outcomes(passed=2)
    assert "@pytest.mark.max_queries(3)" in app.path.joinpath("test_param.py").read_text()
