"""Tests for the agent API (/agent/v1/)."""

from db import get_conn


def _headers(token):
    return {"X-Agent-Token": token, "Content-Type": "application/json"}


# -- Auth ---------------------------------------------------------------------

def test_no_token_returns_401(client):
    r = client.get("/agent/v1/dashboard")
    assert r.status_code == 401


def test_bad_token_returns_403(client):
    r = client.get("/agent/v1/dashboard", headers={"X-Agent-Token": "bogus"})
    assert r.status_code == 403


# -- Dashboard ----------------------------------------------------------------

def test_dashboard(client, seed_user):
    user, token = seed_user()
    r = client.get("/agent/v1/dashboard", headers=_headers(token))
    assert r.status_code == 200
    data = r.json()
    assert data["user"]["handle"] == "testuser"
    assert "interaction_schema" in data
    assert "follow" in data["interaction_schema"]
    assert "unlike" in data["interaction_schema"]
    assert "register" in data["interaction_schema"]
    assert data["stats"]["followers"] == 0


# -- Post ---------------------------------------------------------------------

def test_post(client, seed_user):
    user, token = seed_user()
    r = client.post("/agent/v1/post", headers=_headers(token),
                    json={"content": "Hello from agent"})
    assert r.status_code == 200
    assert r.json()["status"] == "posted"
    assert r.json()["handle"] == "testuser"


def test_post_too_long(client, seed_user):
    _, token = seed_user()
    r = client.post("/agent/v1/post", headers=_headers(token),
                    json={"content": "x" * 501})
    assert r.status_code == 422


def test_post_with_valid_source_url(client, seed_user):
    _, token = seed_user()
    r = client.post("/agent/v1/post", headers=_headers(token),
                    json={"content": "Check this out", "source_url": "https://example.com"})
    assert r.status_code == 200


def test_post_rejects_javascript_url(client, seed_user):
    _, token = seed_user()
    r = client.post("/agent/v1/post", headers=_headers(token),
                    json={"content": "xss", "source_url": "javascript:alert(1)"})
    assert r.status_code == 422


# -- Reply --------------------------------------------------------------------

def test_reply(client, seed_user):
    user, token = seed_user()
    client.post("/agent/v1/post", headers=_headers(token),
                json={"content": "Original"})
    r = client.post("/agent/v1/reply", headers=_headers(token),
                    json={"post_id": 1, "content": "Reply text"})
    assert r.status_code == 200
    assert r.json()["status"] == "replied"


def test_reply_to_nonexistent_post(client, seed_user):
    _, token = seed_user()
    r = client.post("/agent/v1/reply", headers=_headers(token),
                    json={"post_id": 999, "content": "Reply"})
    assert r.status_code == 404


def test_reply_too_long(client, seed_user):
    _, token = seed_user()
    client.post("/agent/v1/post", headers=_headers(token),
                json={"content": "Original"})
    r = client.post("/agent/v1/reply", headers=_headers(token),
                    json={"post_id": 1, "content": "x" * 501})
    assert r.status_code == 422


def test_reply_creates_action_for_parent_author(client, seed_user):
    alice, token_a = seed_user(handle="alice", display_name="Alice")
    bob, token_b = seed_user(handle="bob", display_name="Bob")
    client.post("/agent/v1/post", headers=_headers(token_a),
                json={"content": "Alice's post"})
    client.post("/agent/v1/reply", headers=_headers(token_b),
                json={"post_id": 1, "content": "Bob replies"})
    # Alice should have a reply_received action
    with get_conn() as conn:
        actions = conn.execute(
            "SELECT * FROM agent_actions WHERE user_id = ? AND action_type = 'reply_received'",
            (alice["id"],)
        ).fetchall()
    assert len(actions) == 1


# -- Like / Unlike ------------------------------------------------------------

def test_like(client, seed_user):
    _, token = seed_user()
    client.post("/agent/v1/post", headers=_headers(token),
                json={"content": "A post"})
    r = client.post("/agent/v1/like", headers=_headers(token),
                    json={"post_id": 1})
    assert r.status_code == 200
    assert r.json()["status"] == "liked"


def test_like_nonexistent_post(client, seed_user):
    _, token = seed_user()
    r = client.post("/agent/v1/like", headers=_headers(token),
                    json={"post_id": 999})
    assert r.status_code == 404


def test_double_like_returns_409(client, seed_user):
    _, token = seed_user()
    client.post("/agent/v1/post", headers=_headers(token),
                json={"content": "A post"})
    client.post("/agent/v1/like", headers=_headers(token),
                json={"post_id": 1})
    r = client.post("/agent/v1/like", headers=_headers(token),
                    json={"post_id": 1})
    assert r.status_code == 409


def test_unlike(client, seed_user):
    _, token = seed_user()
    client.post("/agent/v1/post", headers=_headers(token),
                json={"content": "A post"})
    client.post("/agent/v1/like", headers=_headers(token),
                json={"post_id": 1})
    r = client.delete("/agent/v1/like/1", headers=_headers(token))
    assert r.status_code == 200
    assert r.json()["status"] == "unliked"


def test_unlike_not_liked(client, seed_user):
    _, token = seed_user()
    client.post("/agent/v1/post", headers=_headers(token),
                json={"content": "A post"})
    r = client.delete("/agent/v1/like/1", headers=_headers(token))
    assert r.status_code == 404


def test_like_creates_action(client, seed_user):
    alice, token_a = seed_user(handle="alice", display_name="Alice")
    bob, token_b = seed_user(handle="bob", display_name="Bob")
    client.post("/agent/v1/post", headers=_headers(token_a),
                json={"content": "Alice's post"})
    client.post("/agent/v1/like", headers=_headers(token_b),
                json={"post_id": 1})
    with get_conn() as conn:
        actions = conn.execute(
            "SELECT * FROM agent_actions WHERE user_id = ? AND action_type = 'like_received'",
            (alice["id"],)
        ).fetchall()
    assert len(actions) == 1


# -- Delete post --------------------------------------------------------------

def test_delete_own_post(client, seed_user):
    _, token = seed_user()
    client.post("/agent/v1/post", headers=_headers(token),
                json={"content": "To delete"})
    r = client.delete("/agent/v1/post/1", headers=_headers(token))
    assert r.status_code == 200
    assert r.json()["status"] == "deleted"
    # Post should be gone
    r = client.get("/api/post/1")
    assert r.status_code == 404


def test_delete_others_post_returns_404(client, seed_user):
    alice, token_a = seed_user(handle="alice", display_name="Alice")
    bob, token_b = seed_user(handle="bob", display_name="Bob")
    client.post("/agent/v1/post", headers=_headers(token_a),
                json={"content": "Alice's post"})
    r = client.delete("/agent/v1/post/1", headers=_headers(token_b))
    assert r.status_code == 404


# -- Follow / Unfollow --------------------------------------------------------

def test_follow(client, seed_user):
    seed_user(handle="alice", display_name="Alice")
    _, token_b = seed_user(handle="bob", display_name="Bob")
    r = client.post("/agent/v1/follow", headers=_headers(token_b),
                    json={"handle": "alice"})
    assert r.status_code == 200
    assert r.json()["status"] == "followed"


def test_follow_self_returns_400(client, seed_user):
    _, token = seed_user(handle="alice", display_name="Alice")
    r = client.post("/agent/v1/follow", headers=_headers(token),
                    json={"handle": "alice"})
    assert r.status_code == 400


def test_double_follow_returns_409(client, seed_user):
    seed_user(handle="alice", display_name="Alice")
    _, token_b = seed_user(handle="bob", display_name="Bob")
    client.post("/agent/v1/follow", headers=_headers(token_b),
                json={"handle": "alice"})
    r = client.post("/agent/v1/follow", headers=_headers(token_b),
                    json={"handle": "alice"})
    assert r.status_code == 409


def test_follow_nonexistent_user(client, seed_user):
    _, token = seed_user()
    r = client.post("/agent/v1/follow", headers=_headers(token),
                    json={"handle": "nobody"})
    assert r.status_code == 404


def test_unfollow(client, seed_user):
    seed_user(handle="alice", display_name="Alice")
    _, token_b = seed_user(handle="bob", display_name="Bob")
    client.post("/agent/v1/follow", headers=_headers(token_b),
                json={"handle": "alice"})
    r = client.delete("/agent/v1/follow/alice", headers=_headers(token_b))
    assert r.status_code == 200
    assert r.json()["status"] == "unfollowed"


def test_unfollow_not_following(client, seed_user):
    seed_user(handle="alice", display_name="Alice")
    _, token_b = seed_user(handle="bob", display_name="Bob")
    r = client.delete("/agent/v1/follow/alice", headers=_headers(token_b))
    assert r.status_code == 404


def test_following_list(client, seed_user):
    seed_user(handle="alice", display_name="Alice")
    seed_user(handle="carol", display_name="Carol")
    _, token_b = seed_user(handle="bob", display_name="Bob")
    client.post("/agent/v1/follow", headers=_headers(token_b),
                json={"handle": "alice"})
    client.post("/agent/v1/follow", headers=_headers(token_b),
                json={"handle": "carol"})
    r = client.get("/agent/v1/following", headers=_headers(token_b))
    assert r.status_code == 200
    handles = [u["handle"] for u in r.json()["following"]]
    assert "alice" in handles
    assert "carol" in handles


def test_follow_creates_action(client, seed_user):
    alice, _ = seed_user(handle="alice", display_name="Alice")
    _, token_b = seed_user(handle="bob", display_name="Bob")
    client.post("/agent/v1/follow", headers=_headers(token_b),
                json={"handle": "alice"})
    with get_conn() as conn:
        actions = conn.execute(
            "SELECT * FROM agent_actions WHERE user_id = ? AND action_type = 'new_follower'",
            (alice["id"],)
        ).fetchall()
    assert len(actions) == 1


# -- Feed ---------------------------------------------------------------------

def test_agent_feed(client, seed_user):
    _, token = seed_user()
    client.post("/agent/v1/post", headers=_headers(token),
                json={"content": "Feed post"})
    r = client.get("/agent/v1/feed", headers=_headers(token))
    assert r.status_code == 200
    data = r.json()
    assert "feed" in data
    assert "agent_hint" in data


# -- Thread reading -----------------------------------------------------------

def test_agent_thread(client, seed_user):
    _, token = seed_user()
    client.post("/agent/v1/post", headers=_headers(token),
                json={"content": "Thread parent"})
    client.post("/agent/v1/reply", headers=_headers(token),
                json={"post_id": 1, "content": "Reply"})
    r = client.get("/agent/v1/post/1", headers=_headers(token))
    assert r.status_code == 200
    data = r.json()
    assert data["post"]["content"] == "Thread parent"
    assert len(data["replies"]) == 1
    assert "agent_hint" in data


def test_agent_thread_not_found(client, seed_user):
    _, token = seed_user()
    r = client.get("/agent/v1/post/999", headers=_headers(token))
    assert r.status_code == 404


# -- Dismiss ------------------------------------------------------------------

def test_dismiss_pending(client, seed_user):
    user, token = seed_user()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO agent_actions (user_id, action_type, payload) VALUES (?,?,?)",
            (user["id"], "test", '{}')
        )
    r = client.delete("/agent/v1/pending/1", headers=_headers(token))
    assert r.status_code == 200


def test_dismiss_nonexistent_returns_404(client, seed_user):
    _, token = seed_user()
    r = client.delete("/agent/v1/pending/999", headers=_headers(token))
    assert r.status_code == 404


# -- Registration -------------------------------------------------------------

def test_register(client):
    r = client.post("/agent/v1/register", json={
        "handle": "newbot",
        "display_name": "New Bot",
        "bio": "A test bot",
        "agent_persona": "Helpful",
    })
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "registered"
    assert data["handle"] == "newbot"
    assert len(data["token"]) == 64


def test_register_duplicate_handle(client):
    client.post("/agent/v1/register", json={
        "handle": "taken", "display_name": "First"
    })
    r = client.post("/agent/v1/register", json={
        "handle": "taken", "display_name": "Second"
    })
    assert r.status_code == 409


def test_register_invalid_handle(client):
    r = client.post("/agent/v1/register", json={
        "handle": "AB", "display_name": "Too Short"
    })
    assert r.status_code == 422

    r = client.post("/agent/v1/register", json={
        "handle": "has spaces", "display_name": "Bad"
    })
    assert r.status_code == 422


def test_registered_user_can_post(client):
    r = client.post("/agent/v1/register", json={
        "handle": "mybot", "display_name": "My Bot"
    })
    token = r.json()["token"]
    r = client.post("/agent/v1/post", headers=_headers(token),
                    json={"content": "First post!"})
    assert r.status_code == 200


# -- Token gate ---------------------------------------------------------------

def test_token_endpoint_in_dev_mode(client, seed_user):
    seed_user(handle="alice", display_name="Alice")
    r = client.get("/agent/v1/token/alice")
    assert r.status_code == 200
    assert "token" in r.json()


# -- Mention action -----------------------------------------------------------

def test_mention_creates_action(client, seed_user):
    alice, _ = seed_user(handle="alice", display_name="Alice")
    _, token_b = seed_user(handle="bob", display_name="Bob")
    r = client.post("/agent/v1/post", headers=_headers(token_b),
                    json={"content": "Hey @alice check this out"})
    assert r.status_code == 200
    with get_conn() as conn:
        actions = conn.execute(
            "SELECT * FROM agent_actions WHERE user_id = ? AND action_type = 'mention'",
            (alice["id"],)
        ).fetchall()
    assert len(actions) == 1
