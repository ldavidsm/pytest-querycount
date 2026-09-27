# Releasing

Publication goes through Trusted Publishing, so there is no API token anywhere in
this repository or in its secrets. PyPI verifies the workflow's identity instead.

## One-time setup

1. **PyPI account.** Register at <https://pypi.org/account/register/>, verify the
   email, and enable 2FA -- it is mandatory. Keep the recovery codes somewhere
   other than the password manager holding the TOTP seed; if the phone and the
   codes are in the same place, losing one loses both.

2. **Pending publisher.** The project does not exist on PyPI yet, so it cannot be
   configured from its own settings page. Go to
   <https://pypi.org/manage/account/publishing/> and add a *pending* publisher:

   | Field | Value |
   |---|---|
   | PyPI Project Name | `pytest-querycount` |
   | Owner | `ldavidsm` |
   | Repository name | `pytest-querycount` |
   | Workflow name | `publish.yml` |
   | Environment name | `pypi` |

3. **GitHub environment.** `publish.yml` declares `environment: pypi`, and the job
   fails if it does not exist. Create it under
   Settings -> Environments -> New environment, named exactly `pypi`.

   Or from the command line:

   ```bash
   gh api -X PUT repos/ldavidsm/pytest-querycount/environments/pypi
   ```

4. **Optional: TestPyPI first.** <https://test.pypi.org> is a separate site with a
   separate account and its own pending publisher. Worth doing once, to see the
   rendered project page before the name is spent on the real index.

## Each release

1. Update `CHANGELOG.md`: move what is under `Unreleased` into a new version
   heading with today's date.

2. Bump the version in `src/pytest_querycount/__init__.py`. That is the single
   source of truth -- `pyproject.toml` reads it via `[tool.hatch.version]`.

3. Check it locally, with PostgreSQL running so the plan tests do not skip:

   ```bash
   docker run -d --name qc-pg -e POSTGRES_PASSWORD=querycount \
       -e POSTGRES_DB=querycount -p 55432:5432 postgres:17-alpine

   QUERYCOUNT_REQUIRE_PG=1 pytest
   ruff check src tests && ruff format --check src tests && mypy
   python -m build && python -m twine check --strict dist/*
   ```

4. Commit, tag, push:

   ```bash
   git commit -am "Release 0.3.0"
   git tag -a v0.3.0 -m "Release 0.3.0"
   git push && git push --tags
   ```

5. Publish a GitHub Release on that tag. That is what fires `publish.yml`; a tag
   on its own does nothing.

6. Confirm at <https://pypi.org/project/pytest-querycount/>, then install it
   somewhere clean and run it once. A green publish job is not the same as a
   working package:

   ```bash
   python -m venv /tmp/check && /tmp/check/bin/pip install "pytest-querycount[postgresql]"
   ```

## If a release goes wrong

A version on PyPI cannot be replaced, only yanked -- so a bad `0.3.0` becomes
`0.3.1`, never a second `0.3.0`. Yank from the project's release page; yanking
hides it from resolvers while leaving it installable for anyone who pinned it.
