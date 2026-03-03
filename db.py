"""
SQLite database setup and helpers for agent-social.
"""

import contextlib
import os
import secrets
import sqlite3
from pathlib import Path

_data_dir = Path(os.environ.get("DATA_DIR", Path(__file__).parent))
DB_PATH = _data_dir / "social.db"


@contextlib.contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            handle          TEXT    UNIQUE NOT NULL,
            display_name    TEXT    NOT NULL,
            bio             TEXT    DEFAULT '',
            avatar_prompt   TEXT    DEFAULT '',
            header_prompt   TEXT    DEFAULT '',
            agent_persona   TEXT    DEFAULT '',
            activation_code TEXT    DEFAULT NULL,
            agent_active    INTEGER DEFAULT 1,
            created_at      TEXT    DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS posts (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL REFERENCES users(id),
            content     TEXT    NOT NULL,
            parent_id   INTEGER REFERENCES posts(id),
            source_url  TEXT    DEFAULT NULL,
            posted_by   TEXT    DEFAULT 'agent' CHECK(posted_by IN ('agent','human')),
            created_at  TEXT    DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS likes (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL REFERENCES users(id),
            post_id     INTEGER NOT NULL REFERENCES posts(id),
            created_at  TEXT    DEFAULT (datetime('now')),
            UNIQUE(user_id, post_id)
        );

        CREATE TABLE IF NOT EXISTS follows (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            follower_id  INTEGER NOT NULL REFERENCES users(id),
            following_id INTEGER NOT NULL REFERENCES users(id),
            created_at   TEXT    DEFAULT (datetime('now')),
            UNIQUE(follower_id, following_id),
            CHECK(follower_id != following_id)
        );

        CREATE TABLE IF NOT EXISTS agent_actions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL REFERENCES users(id),
            action_type TEXT    NOT NULL,
            payload     TEXT    NOT NULL,
            status      TEXT    DEFAULT 'pending' CHECK(status IN ('pending','done','skipped')),
            created_at  TEXT    DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS agent_tokens (
            token       TEXT    PRIMARY KEY,
            user_id     INTEGER NOT NULL REFERENCES users(id),
            created_at  TEXT    DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS user_context (
            user_id     INTEGER PRIMARY KEY REFERENCES users(id),
            context     TEXT    NOT NULL DEFAULT '',
            updated_at  TEXT    DEFAULT (datetime('now'))
        );

        -- Performance indexes
        CREATE INDEX IF NOT EXISTS idx_posts_user_id ON posts(user_id);
        CREATE INDEX IF NOT EXISTS idx_posts_parent_id ON posts(parent_id);
        CREATE INDEX IF NOT EXISTS idx_posts_created_at ON posts(created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_posts_user_created ON posts(user_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_likes_post_id ON likes(post_id);
        CREATE INDEX IF NOT EXISTS idx_likes_user_post ON likes(user_id, post_id);
        CREATE INDEX IF NOT EXISTS idx_follows_follower ON follows(follower_id);
        CREATE INDEX IF NOT EXISTS idx_follows_following ON follows(following_id);
        CREATE INDEX IF NOT EXISTS idx_agent_actions_user_status ON agent_actions(user_id, status);
        CREATE INDEX IF NOT EXISTS idx_agent_tokens_user_id ON agent_tokens(user_id);
        """)


def make_token() -> str:
    return secrets.token_hex(32)


def make_activation_code() -> str:
    """Generate a short human-readable activation code (12 chars, uppercase)."""
    return secrets.token_hex(6).upper()
