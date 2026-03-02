import os
import pytest

# Use a separate test database
os.environ["AGENT_SOCIAL_ENV"] = "dev"

import db as db_module
from db import init_db, get_conn, make_token


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    """Use a fresh SQLite database for each test."""
    test_db = tmp_path / "test.db"
    db_module.DB_PATH = test_db
    init_db()
    yield test_db
    if test_db.exists():
        test_db.unlink()


@pytest.fixture
def seed_user():
    """Create a single user and return (user_dict, token)."""
    def _seed(handle="testuser", display_name="Test User", bio="", persona=""):
        with get_conn() as conn:
            cur = conn.execute(
                "INSERT INTO users (handle, display_name, bio, agent_persona) VALUES (?,?,?,?)",
                (handle, display_name, bio, persona)
            )
            uid = cur.lastrowid
            token = make_token()
            conn.execute("INSERT INTO agent_tokens (token, user_id) VALUES (?,?)", (token, uid))
        return {"id": uid, "handle": handle, "display_name": display_name}, token
    return _seed


@pytest.fixture
def client():
    """Starlette TestClient for the FastAPI app."""
    from starlette.testclient import TestClient
    import importlib
    import main as main_module
    importlib.reload(main_module)
    main_module._cached_html = None

    with TestClient(main_module.app) as c:
        yield c
