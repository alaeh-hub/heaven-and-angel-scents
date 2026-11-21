"""Shared fixtures for the integration tests.

Tests hit real routes through Flask's test client against a disposable MySQL
database (this app has no ORM to mock). Needs a reachable MySQL server (see
config.py defaults; override with MYSQL_* env vars) and a `mysql` CLI to load
schema.sql, whose DELIMITER procedure blocks mysql-connector cannot run.
Tests never touch the real MYSQL_DB: a throwaway database (default
heaven_and_angel_scents_test) is created per session and dropped afterwards.
"""
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

TEST_DB_NAME = os.environ.get("TEST_MYSQL_DB", "heaven_and_angel_scents_test")

# Must run before `config`/`app` is imported (Config reads os.environ once).
# MYSQL_DB is force-set so tests can never hit a real database.
os.environ["MYSQL_DB"] = TEST_DB_NAME
os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("FLASK_DEBUG", "1")
os.environ.setdefault("SECRET_KEY", "pytest-secret-key-not-for-real-use")
os.environ.setdefault("PARTNER_PORTAL_SLUG", "pytest-portal-slug")
os.environ.setdefault("GEMINI_API_KEY", "")
# Rate limits off, or the suite itself exhausts them (e.g. login's 10 per minute).
os.environ.setdefault("RATELIMIT_ENABLED", "0")

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_SQL = REPO_ROOT / "schema.sql"


def _find_mysql_client():
    """Locate the `mysql` CLI: $MYSQL_CLIENT, then PATH, then common XAMPP paths."""
    override = os.environ.get("MYSQL_CLIENT")
    if override:
        return override
    found = shutil.which("mysql")
    if found:
        return found
    for drive in "CDEF":
        candidate = Path(f"{drive}:/xampp/mysql/bin/mysql.exe")
        if candidate.is_file():
            return str(candidate)
    return None


def _mysql_admin_connection():
    import mysql.connector
    return mysql.connector.connect(
        host=os.environ.get("MYSQL_HOST", "localhost"),
        port=int(os.environ.get("MYSQL_PORT", 3306)),
        user=os.environ.get("MYSQL_USER", "root"),
        password=os.environ.get("MYSQL_PASSWORD", ""),
    )


@pytest.fixture(scope="session", autouse=True)
def _test_database():
    """Create the test database from schema.sql once per session, drop it afterwards.

    schema.sql hardcodes its database name; a throwaway copy swaps in the test
    database name so the real one is never used.
    """
    mysql_client = _find_mysql_client()
    if not mysql_client:
        pytest.skip(
            "No mysql client found (checked $MYSQL_CLIENT, PATH, and common XAMPP "
            "install paths) — needed to load schema.sql, which uses DELIMITER-based "
            "stored-procedure migration blocks that mysql-connector-python can't "
            "execute directly. Set MYSQL_CLIENT to your mysql binary's path to run "
            "these tests."
        )

    try:
        import mysql.connector  # noqa: F401  (checked separately from the
        # connection attempt below, so a missing dependency gets its own message)
    except ImportError:
        pytest.skip(
            "mysql-connector-python isn't installed in this Python environment "
            f"({sys.executable}) — these tests need the project's own dependencies "
            "(requirements.txt). This usually means pytest "
            "was run outside the project's virtual environment: use "
            "`.venv\\Scripts\\python.exe -m pytest` (Windows) or "
            "`.venv/bin/python -m pytest` rather than a bare `pytest` command, "
            "which can resolve to a different, unrelated Python install on PATH."
        )

    try:
        admin_conn = _mysql_admin_connection()
    except Exception as exc:
        pytest.skip(
            f"Couldn't reach a MySQL server to run these tests against: {exc}")

    cur = admin_conn.cursor()
    cur.execute(f"DROP DATABASE IF EXISTS `{TEST_DB_NAME}`")
    admin_conn.commit()
    cur.close()
    admin_conn.close()

    schema_sql = SCHEMA_SQL.read_text(encoding="utf-8")
    test_schema_sql = schema_sql.replace(
        "heaven_and_angel_scents", TEST_DB_NAME)
    tmp_path = REPO_ROOT / f"_test_schema_{uuid.uuid4().hex}.sql"
    tmp_path.write_text(test_schema_sql, encoding="utf-8")
    try:
        cmd = [
            mysql_client,
            "-h", os.environ.get("MYSQL_HOST", "localhost"),
            "-P", str(os.environ.get("MYSQL_PORT", 3306)),
            "-u", os.environ.get("MYSQL_USER", "root"),
        ]
        password = os.environ.get("MYSQL_PASSWORD", "")
        if password:
            cmd.append(f"-p{password}")
        with open(tmp_path, "rb") as f:
            result = subprocess.run(cmd, stdin=f, capture_output=True)
        if result.returncode != 0:
            pytest.fail(
                "Failed to load schema.sql into the test database:\n"
                + result.stderr.decode(errors="replace")
            )
    finally:
        tmp_path.unlink(missing_ok=True)

    yield

    admin_conn = _mysql_admin_connection()
    cur = admin_conn.cursor()
    cur.execute(f"DROP DATABASE IF EXISTS `{TEST_DB_NAME}`")
    admin_conn.commit()
    cur.close()
    admin_conn.close()


@pytest.fixture(scope="session")
def app():
    from heaven_angel.app import create_app
    application = create_app()
    application.config.update(
        TESTING=True,
        # Flask-WTF's documented way to test CSRF forms (read per request).
        WTF_CSRF_ENABLED=False,
    )
    return application


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def sql():
    """Plain mysql-connector connection to the test database, separate from the app's.

    Used to set up rows and assert on real DB state. autocommit=True so a bare
    SELECT never leaves a REPEATABLE READ transaction open and later reads a
    stale snapshot from before a route's own commit.
    """
    import mysql.connector
    conn = mysql.connector.connect(
        host=os.environ.get("MYSQL_HOST", "localhost"),
        port=int(os.environ.get("MYSQL_PORT", 3306)),
        user=os.environ.get("MYSQL_USER", "root"),
        password=os.environ.get("MYSQL_PASSWORD", ""),
        database=TEST_DB_NAME,
        autocommit=True,
    )
    yield conn
    conn.close()
