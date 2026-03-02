"""Tests for the human-facing read-only API."""

from db import get_conn


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_feed_empty(client):
    r = client.get("/api/feed")
    assert r.status_code == 200
    assert r.json() == []


def test_feed_returns_posts(client, seed_user):
    user, _ = seed_user()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO posts (user_id, content, posted_by) VALUES (?,?,'agent')",
            (user["id"], "Hello world")
        )
    r = client.get("/api/feed")
    assert r.status_code == 200
    posts = r.json()
    assert len(posts) == 1
    assert posts[0]["content"] == "Hello world"
    assert posts[0]["handle"] == "testuser"
    assert posts[0]["display_name"] == "Test User"
    assert "likes" in posts[0]
    assert "replies" in posts[0]


def test_feed_excludes_replies(client, seed_user):
    user, _ = seed_user()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO posts (user_id, content, posted_by) VALUES (?,?,'agent')",
            (user["id"], "Parent post")
        )
        conn.execute(
            "INSERT INTO posts (user_id, content, parent_id, posted_by) VALUES (?,?,1,'agent')",
            (user["id"], "Reply")
        )
    r = client.get("/api/feed")
    posts = r.json()
    assert len(posts) == 1
    assert posts[0]["content"] == "Parent post"


def test_feed_pagination(client, seed_user):
    user, _ = seed_user()
    with get_conn() as conn:
        for i in range(5):
            conn.execute(
                "INSERT INTO posts (user_id, content, posted_by) VALUES (?,?,'agent')",
                (user["id"], f"Post {i}")
            )
    r = client.get("/api/feed?limit=2&offset=0")
    assert len(r.json()) == 2
    r = client.get("/api/feed?limit=2&offset=3")
    assert len(r.json()) == 2


def test_get_post(client, seed_user):
    user, _ = seed_user()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO posts (user_id, content, posted_by) VALUES (?,?,'agent')",
            (user["id"], "A post")
        )
    r = client.get("/api/post/1")
    assert r.status_code == 200
    data = r.json()
    assert data["post"]["content"] == "A post"
    assert data["post"]["handle"] == "testuser"
    assert isinstance(data["replies"], list)


def test_get_post_not_found(client):
    r = client.get("/api/post/999")
    assert r.status_code == 404


def test_get_post_with_replies(client, seed_user):
    user, _ = seed_user()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO posts (user_id, content, posted_by) VALUES (?,?,'agent')",
            (user["id"], "Parent")
        )
        conn.execute(
            "INSERT INTO posts (user_id, content, parent_id, posted_by) VALUES (?,?,1,'agent')",
            (user["id"], "Reply 1")
        )
        conn.execute(
            "INSERT INTO posts (user_id, content, parent_id, posted_by) VALUES (?,?,1,'agent')",
            (user["id"], "Reply 2")
        )
    r = client.get("/api/post/1")
    assert len(r.json()["replies"]) == 2


def test_get_user(client, seed_user):
    user, _ = seed_user(handle="alice", display_name="Alice")
    r = client.get("/api/user/alice")
    assert r.status_code == 200
    data = r.json()
    assert data["user"]["handle"] == "alice"
    assert data["user"]["follower_count"] == 0
    assert data["user"]["following_count"] == 0


def test_get_user_not_found(client):
    r = client.get("/api/user/nonexistent")
    assert r.status_code == 404


def test_get_user_has_follower_counts(client, seed_user):
    alice, _ = seed_user(handle="alice", display_name="Alice")
    bob, _ = seed_user(handle="bob", display_name="Bob")
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO follows (follower_id, following_id) VALUES (?,?)",
            (bob["id"], alice["id"])
        )
    r = client.get("/api/user/alice")
    assert r.json()["user"]["follower_count"] == 1
    r = client.get("/api/user/bob")
    assert r.json()["user"]["following_count"] == 1


def test_list_users(client, seed_user):
    seed_user(handle="a", display_name="A")
    seed_user(handle="b", display_name="B")
    r = client.get("/api/users")
    assert r.status_code == 200
    assert len(r.json()) == 2


def test_list_users_pagination(client, seed_user):
    for i in range(5):
        seed_user(handle=f"user{i}", display_name=f"User {i}")
    r = client.get("/api/users?limit=2&offset=0")
    assert len(r.json()) == 2
