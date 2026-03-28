"""
Pytest configuration and shared fixtures.

Environment variables must be set before any app module is imported,
because app.py runs initialization (Sentry, secret-key checks, etc.)
at module level. python-dotenv's load_dotenv() does NOT override
already-set env vars, so these take precedence over any .env file.
"""
import os
import tempfile

os.environ["FLASK_ENV"] = "development"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-pytest-only-not-production")
os.environ.setdefault("SENTRY_DSN", "")
os.environ.setdefault("GEMINI_API_KEY", "test-gemini-key")
os.environ.setdefault("OPENAI_API_KEY", "test-openai-key")
os.environ.setdefault("ALLOW_USER_API_KEYS", "false")
os.environ.setdefault("FERNET_KEY", "")

import pytest
from datetime import timedelta
from werkzeug.security import generate_password_hash

# Import after env vars are set
from app import app as flask_app, _rate_limit_hits  # noqa: E402
from db import db, User  # noqa: E402
from billing_service import utc_now  # noqa: E402

# ── Single file-based SQLite database shared across the whole test session ────
#
# StaticPool (in-memory) cannot be shared across multiple SQLAlchemy sessions
# because concurrent transactions on a single connection conflict in SQLite.
# A named file-based database is visible to all sessions independently.
_DB_FILE = tempfile.mktemp(suffix=".db", prefix="ecomfans_test_")
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_FILE}"

flask_app.config.update(
    {
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{_DB_FILE}",
        "SQLALCHEMY_ENGINE_OPTIONS": {"connect_args": {"check_same_thread": False}},
    }
)


@pytest.fixture(scope="session", autouse=True)
def _create_tables():
    """Create all DB tables once at the start of the session."""
    with flask_app.app_context():
        db.create_all()
    yield
    # Drop tables and delete the temp file after the full session
    with flask_app.app_context():
        db.drop_all()
    try:
        os.unlink(_DB_FILE)
    except OSError:
        pass


@pytest.fixture(autouse=True)
def _clean_tables():
    """Truncate all rows between tests for isolation."""
    yield
    with flask_app.app_context():
        for table in reversed(db.metadata.sorted_tables):
            db.session.execute(table.delete())
        db.session.commit()


@pytest.fixture()
def app():
    return flask_app


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def test_user(app):
    """A basic active user with password 'Password1!'."""
    with app.app_context():
        user = User(
            username="testuser",
            email="test@example.com",
            password_hash=generate_password_hash("Password1!"),
            plan_tier="free",
            credits=30,
            extra_credits=0,
            next_credit_reset_at=utc_now() + timedelta(days=30),
        )
        db.session.add(user)
        db.session.commit()
        user_id = user.id
    return user_id  # Return ID only — objects must be re-fetched in each context


@pytest.fixture(autouse=True)
def clear_rate_limits():
    """
    Reset in-memory rate limit state before each test so login/register
    tests don't exhaust the bucket for subsequent tests in the session.
    """
    _rate_limit_hits.clear()
    yield
    _rate_limit_hits.clear()


def csrf_token(client) -> str:
    """
    Trigger the before_request hook by hitting any GET endpoint so the
    session is initialised with a CSRF token, then return it.
    """
    client.get("/login")
    with client.session_transaction() as sess:
        return sess.get("_csrf_token", "")


def post_with_csrf(client, path: str, data: dict) -> object:
    """POST *data* to *path* with the session's CSRF token injected."""
    token = csrf_token(client)
    data["_csrf_token"] = token
    return client.post(path, data=data, follow_redirects=True)
