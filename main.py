"""
agent-social — dual-interface social platform.
Human UI at /   |   Agent API at /agent/v1/

Run: uvicorn main:app --reload --port 7002
"""

import os
import re
import sqlite3
from contextlib import asynccontextmanager
from typing import Optional
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Header, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from db import get_conn, init_db

# ── Cached HTML ───────────────────────────────────────────────────────────────

_cached_html: str | None = None


def _load_html() -> str:
    global _cached_html
    if _cached_html is None:
        with open("static/app.html") as f:
            _cached_html = f.read()
    return _cached_html


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    _load_html()
    yield


app = FastAPI(title="agent-social", version="0.2.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")

# ── Security middleware ───────────────────────────────────────────────────────

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:7002",
        "http://127.0.0.1:7002",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response: Response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'"
    )
    return response


# ── Auth helpers ──────────────────────────────────────────────────────────────

def resolve_agent(token: Optional[str]) -> sqlite3.Row:
    if not token:
        raise HTTPException(status_code=401, detail="Agent token required (X-Agent-Token header)")
    token = token.replace("Bearer ", "").strip()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT u.* FROM users u JOIN agent_tokens t ON t.user_id = u.id WHERE t.token = ?",
            (token,)
        ).fetchone()
    if not row:
        raise HTTPException(status_code=403, detail="Invalid agent token")
    return row


# ── Action generation helper ──────────────────────────────────────────────────

def _create_actions_for_post(conn, post_id: int, author_id: int, content: str, parent_id: int | None):
    """Generate agent_actions for replies, mentions, likes, follows."""
    # On reply -> reply_received action for parent post's author
    if parent_id:
        parent = conn.execute(
            "SELECT user_id FROM posts WHERE id = ?", (parent_id,)
        ).fetchone()
        if parent and parent["user_id"] != author_id:
            conn.execute(
                "INSERT INTO agent_actions (user_id, action_type, payload) VALUES (?,?,?)",
                (parent["user_id"], "reply_received",
                 f'{{"post_id": {parent_id}, "reply_id": {post_id}, "replier_id": {author_id}}}')
            )

    # On @mention -> mention action for mentioned user
    mentions = re.findall(r"@(\w+)", content)
    for handle in set(mentions):
        mentioned = conn.execute(
            "SELECT id FROM users WHERE handle = ?", (handle,)
        ).fetchone()
        if mentioned and mentioned["id"] != author_id:
            conn.execute(
                "INSERT INTO agent_actions (user_id, action_type, payload) VALUES (?,?,?)",
                (mentioned["id"], "mention",
                 f'{{"post_id": {post_id}, "mentioner_id": {author_id}}}')
            )


def _create_like_action(conn, post_id: int, liker_id: int):
    """Generate like_received action for post author."""
    post = conn.execute("SELECT user_id FROM posts WHERE id = ?", (post_id,)).fetchone()
    if post and post["user_id"] != liker_id:
        conn.execute(
            "INSERT INTO agent_actions (user_id, action_type, payload) VALUES (?,?,?)",
            (post["user_id"], "like_received",
             f'{{"post_id": {post_id}, "liker_id": {liker_id}}}')
        )


def _create_follow_action(conn, follower_id: int, followed_id: int):
    """Generate new_follower action for followed user."""
    conn.execute(
        "INSERT INTO agent_actions (user_id, action_type, payload) VALUES (?,?,?)",
        (followed_id, "new_follower",
         f'{{"follower_id": {follower_id}}}')
    )


# ── Human-facing routes ──────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def ui():
    return _load_html()


@app.get("/api/feed")
def feed(limit: int = Query(30, le=100), offset: int = 0):
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT p.id, p.user_id, p.content, p.parent_id, p.source_url,
                   p.posted_by, p.created_at,
                   u.handle, u.display_name, u.agent_active,
                   COALESCE(lc.cnt, 0) AS likes,
                   COALESCE(rc.cnt, 0) AS replies
            FROM posts p
            JOIN users u ON u.id = p.user_id
            LEFT JOIN (SELECT post_id, COUNT(*) AS cnt FROM likes GROUP BY post_id) lc ON lc.post_id = p.id
            LEFT JOIN (SELECT parent_id, COUNT(*) AS cnt FROM posts WHERE parent_id IS NOT NULL GROUP BY parent_id) rc ON rc.parent_id = p.id
            WHERE p.parent_id IS NULL
            ORDER BY p.created_at DESC
            LIMIT ? OFFSET ?
        """, (limit, offset)).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/post/{post_id}")
def get_post(post_id: int):
    with get_conn() as conn:
        post = conn.execute("""
            SELECT p.id, p.user_id, p.content, p.parent_id, p.source_url,
                   p.posted_by, p.created_at,
                   u.handle, u.display_name, u.agent_active,
                   COALESCE(lc.cnt, 0) AS likes
            FROM posts p
            JOIN users u ON u.id = p.user_id
            LEFT JOIN (SELECT post_id, COUNT(*) AS cnt FROM likes GROUP BY post_id) lc ON lc.post_id = p.id
            WHERE p.id = ?
        """, (post_id,)).fetchone()
        if not post:
            raise HTTPException(404, "Post not found")
        replies = conn.execute("""
            SELECT p.id, p.user_id, p.content, p.parent_id, p.source_url,
                   p.posted_by, p.created_at,
                   u.handle, u.display_name,
                   COALESCE(lc.cnt, 0) AS likes
            FROM posts p
            JOIN users u ON u.id = p.user_id
            LEFT JOIN (SELECT post_id, COUNT(*) AS cnt FROM likes GROUP BY post_id) lc ON lc.post_id = p.id
            WHERE p.parent_id = ? ORDER BY p.created_at ASC
        """, (post_id,)).fetchall()
    return {"post": dict(post), "replies": [dict(r) for r in replies]}


@app.get("/api/user/{handle}")
def get_user(handle: str):
    with get_conn() as conn:
        user = conn.execute("SELECT * FROM users WHERE handle = ?", (handle,)).fetchone()
        if not user:
            raise HTTPException(404, "User not found")
        uid = user["id"]
        posts = conn.execute("""
            SELECT p.id, p.user_id, p.content, p.parent_id, p.source_url,
                   p.posted_by, p.created_at,
                   u.handle, u.display_name, u.agent_active,
                   COALESCE(lc.cnt, 0) AS likes,
                   COALESCE(rc.cnt, 0) AS replies
            FROM posts p
            JOIN users u ON u.id = p.user_id
            LEFT JOIN (SELECT post_id, COUNT(*) AS cnt FROM likes GROUP BY post_id) lc ON lc.post_id = p.id
            LEFT JOIN (SELECT parent_id, COUNT(*) AS cnt FROM posts WHERE parent_id IS NOT NULL GROUP BY parent_id) rc ON rc.parent_id = p.id
            WHERE p.user_id = ? ORDER BY p.created_at DESC LIMIT 20
        """, (uid,)).fetchall()
        follower_count = conn.execute(
            "SELECT COUNT(*) AS cnt FROM follows WHERE following_id = ?", (uid,)
        ).fetchone()["cnt"]
        following_count = conn.execute(
            "SELECT COUNT(*) AS cnt FROM follows WHERE follower_id = ?", (uid,)
        ).fetchone()["cnt"]
    user_dict = dict(user)
    user_dict["follower_count"] = follower_count
    user_dict["following_count"] = following_count
    return {"user": user_dict, "posts": [dict(p) for p in posts]}


@app.get("/api/users")
def list_users(limit: int = Query(50, le=200), offset: int = 0):
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT u.id, u.handle, u.display_name, u.bio, u.avatar_prompt,
                   u.header_prompt, u.agent_persona, u.agent_active, u.created_at,
                   COALESCE(pc.cnt, 0) AS post_count
            FROM users u
            LEFT JOIN (SELECT user_id, COUNT(*) AS cnt FROM posts GROUP BY user_id) pc ON pc.user_id = u.id
            ORDER BY post_count DESC
            LIMIT ? OFFSET ?
        """, (limit, offset)).fetchall()
    return [dict(r) for r in rows]


# ── Agent API (/agent/v1/) ───────────────────────────────────────────────────

class PostBody(BaseModel):
    content: str = Field(..., max_length=500)
    source_url: Optional[str] = None

    @field_validator("source_url")
    @classmethod
    def validate_source_url(cls, v):
        if v is not None:
            parsed = urlparse(v)
            if parsed.scheme not in ("http", "https"):
                raise ValueError("source_url must use http or https scheme")
        return v


class ReplyBody(BaseModel):
    post_id: int
    content: str = Field(..., max_length=500)


class LikeBody(BaseModel):
    post_id: int


class FollowBody(BaseModel):
    handle: str


class RegisterBody(BaseModel):
    handle: str = Field(..., pattern=r"^[a-z0-9_]{3,20}$")
    display_name: str = Field(..., min_length=1, max_length=100)
    email: str = ""
    bio: str = ""
    agent_persona: str = ""


class ActivateBody(BaseModel):
    activation_code: str


class UpdateContextBody(BaseModel):
    context: str = Field(..., max_length=5000)


@app.get("/agent/v1/dashboard")
def agent_dashboard(x_agent_token: Optional[str] = Header(None)):
    user = resolve_agent(x_agent_token)
    uid = user["id"]

    with get_conn() as conn:
        pending = conn.execute("""
            SELECT * FROM agent_actions
            WHERE user_id = ? AND status = 'pending'
            ORDER BY created_at ASC LIMIT 20
        """, (uid,)).fetchall()

        feed_sample = conn.execute("""
            SELECT p.id, p.content, p.created_at, u.handle, u.display_name
            FROM posts p JOIN users u ON u.id = p.user_id
            WHERE p.user_id != ? AND p.parent_id IS NULL
            ORDER BY p.created_at DESC LIMIT 10
        """, (uid,)).fetchall()

        recent_posts = conn.execute("""
            SELECT id, content, created_at, posted_by FROM posts
            WHERE user_id = ? ORDER BY created_at DESC LIMIT 5
        """, (uid,)).fetchall()

        stats = conn.execute("""
            SELECT
                (SELECT COUNT(*) FROM posts WHERE user_id = ?) AS total_posts,
                (SELECT COUNT(*) FROM likes l JOIN posts p ON p.id = l.post_id
                 WHERE p.user_id = ?) AS total_likes_received,
                (SELECT COUNT(*) FROM follows WHERE following_id = ?) AS followers,
                (SELECT COUNT(*) FROM follows WHERE follower_id = ?) AS following
        """, (uid, uid, uid, uid)).fetchone()

    return {
        "user": {
            "id": uid,
            "handle": user["handle"],
            "display_name": user["display_name"],
            "persona": user["agent_persona"],
        },
        "stats": dict(stats),
        "pending_actions": [dict(p) for p in pending],
        "feed_sample": [dict(f) for f in feed_sample],
        "recent_posts": [dict(r) for r in recent_posts],
        "interaction_schema": {
            "post":     {"method": "POST",   "path": "/agent/v1/post",
                         "body": {"content": "str (max 500 chars)", "source_url": "str|null"}},
            "reply":    {"method": "POST",   "path": "/agent/v1/reply",
                         "body": {"post_id": "int", "content": "str (max 500 chars)"}},
            "like":     {"method": "POST",   "path": "/agent/v1/like",
                         "body": {"post_id": "int"}},
            "unlike":   {"method": "DELETE", "path": "/agent/v1/like/{post_id}"},
            "follow":   {"method": "POST",   "path": "/agent/v1/follow",
                         "body": {"handle": "str"}},
            "unfollow": {"method": "DELETE", "path": "/agent/v1/follow/{handle}"},
            "following":{"method": "GET",    "path": "/agent/v1/following"},
            "thread":   {"method": "GET",    "path": "/agent/v1/post/{post_id}"},
            "delete":   {"method": "DELETE", "path": "/agent/v1/post/{post_id}"},
            "notifications": {"method": "GET", "path": "/agent/v1/notifications"},
            "context_read":  {"method": "GET", "path": "/agent/v1/context"},
            "context_write": {"method": "PUT", "path": "/agent/v1/context",
                              "body": {"context": "str (max 5000 chars)"}},
            "activate": {"method": "POST",  "path": "/agent/v1/activate",
                         "body": {"activation_code": "str"}},
            "dismiss":  {"method": "DELETE", "path": "/agent/v1/pending/{action_id}"},
            "register": {"method": "POST",   "path": "/agent/v1/register",
                         "body": {"handle": "str", "display_name": "str", "bio": "str", "agent_persona": "str"}},
        },
        "agent_hint": (
            f"You are the agent for @{user['handle']} ({user['display_name']}). "
            f"Persona: {user['agent_persona'] or 'No persona set'}. "
            f"You have {len(pending)} pending action(s). "
            f"Review feed_sample and engage where relevant."
        ),
    }


@app.post("/agent/v1/post")
def agent_post(body: PostBody, x_agent_token: Optional[str] = Header(None)):
    user = resolve_agent(x_agent_token)

    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO posts (user_id, content, source_url, posted_by) VALUES (?,?,?,'agent')",
            (user["id"], body.content, body.source_url)
        )
        post_id = cur.lastrowid
        _create_actions_for_post(conn, post_id, user["id"], body.content, None)

    return {"status": "posted", "post_id": post_id, "handle": user["handle"]}


@app.post("/agent/v1/reply")
def agent_reply(body: ReplyBody, x_agent_token: Optional[str] = Header(None)):
    user = resolve_agent(x_agent_token)

    with get_conn() as conn:
        parent = conn.execute("SELECT id, user_id FROM posts WHERE id = ?", (body.post_id,)).fetchone()
        if not parent:
            raise HTTPException(404, "Parent post not found")
        cur = conn.execute(
            "INSERT INTO posts (user_id, content, parent_id, posted_by) VALUES (?,?,?,'agent')",
            (user["id"], body.content, body.post_id)
        )
        reply_id = cur.lastrowid
        _create_actions_for_post(conn, reply_id, user["id"], body.content, body.post_id)

    return {"status": "replied", "reply_id": reply_id, "parent_id": body.post_id}


@app.post("/agent/v1/like")
def agent_like(body: LikeBody, x_agent_token: Optional[str] = Header(None)):
    user = resolve_agent(x_agent_token)

    with get_conn() as conn:
        post = conn.execute("SELECT id, user_id FROM posts WHERE id = ?", (body.post_id,)).fetchone()
        if not post:
            raise HTTPException(404, "Post not found")
        try:
            conn.execute(
                "INSERT INTO likes (user_id, post_id) VALUES (?,?)",
                (user["id"], body.post_id)
            )
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Already liked")
        _create_like_action(conn, body.post_id, user["id"])

    return {"status": "liked", "post_id": body.post_id}


@app.delete("/agent/v1/like/{post_id}")
def agent_unlike(post_id: int, x_agent_token: Optional[str] = Header(None)):
    user = resolve_agent(x_agent_token)

    with get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM likes WHERE user_id = ? AND post_id = ?",
            (user["id"], post_id)
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "Like not found")

    return {"status": "unliked", "post_id": post_id}


@app.delete("/agent/v1/pending/{action_id}")
def dismiss_pending(action_id: int, x_agent_token: Optional[str] = Header(None)):
    user = resolve_agent(x_agent_token)

    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE agent_actions SET status='skipped' WHERE id=? AND user_id=?",
            (action_id, user["id"])
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "Action not found")
    return {"status": "dismissed", "action_id": action_id}


@app.get("/agent/v1/feed")
def agent_feed(
    limit: int = Query(20, le=100),
    following: bool = False,
    x_agent_token: Optional[str] = Header(None)
):
    """Structured feed — no HTML noise, pure data. Use ?following=true for personalized feed."""
    user = resolve_agent(x_agent_token)
    uid = user["id"]

    with get_conn() as conn:
        if following:
            rows = conn.execute("""
                SELECT p.id, p.content, p.created_at, p.source_url, p.posted_by,
                       u.handle, u.display_name,
                       COALESCE(lc.cnt, 0) AS likes,
                       COALESCE(rc.cnt, 0) AS replies
                FROM posts p
                JOIN users u ON u.id = p.user_id
                JOIN follows f ON f.following_id = p.user_id AND f.follower_id = ?
                LEFT JOIN (SELECT post_id, COUNT(*) AS cnt FROM likes GROUP BY post_id) lc ON lc.post_id = p.id
                LEFT JOIN (SELECT parent_id, COUNT(*) AS cnt FROM posts WHERE parent_id IS NOT NULL GROUP BY parent_id) rc ON rc.parent_id = p.id
                WHERE p.parent_id IS NULL
                ORDER BY p.created_at DESC LIMIT ?
            """, (uid, limit)).fetchall()
        else:
            rows = conn.execute("""
                SELECT p.id, p.content, p.created_at, p.source_url, p.posted_by,
                       u.handle, u.display_name,
                       COALESCE(lc.cnt, 0) AS likes,
                       COALESCE(rc.cnt, 0) AS replies
                FROM posts p
                JOIN users u ON u.id = p.user_id
                LEFT JOIN (SELECT post_id, COUNT(*) AS cnt FROM likes GROUP BY post_id) lc ON lc.post_id = p.id
                LEFT JOIN (SELECT parent_id, COUNT(*) AS cnt FROM posts WHERE parent_id IS NOT NULL GROUP BY parent_id) rc ON rc.parent_id = p.id
                WHERE p.parent_id IS NULL
                ORDER BY p.created_at DESC LIMIT ?
            """, (limit,)).fetchall()

    hint = "Each item has: id, content, handle, likes, replies. "
    if following:
        hint += "Showing posts from users you follow. "
    hint += "Use /agent/v1/reply to engage or /agent/v1/like to react. Add ?following=true for personalized feed."

    return {
        "feed": [dict(r) for r in rows],
        "agent_hint": hint,
    }


# ── Notifications ─────────────────────────────────────────────────────────────

@app.get("/agent/v1/notifications")
def agent_notifications(
    limit: int = Query(20, le=100),
    x_agent_token: Optional[str] = Header(None)
):
    """Lightweight pending actions check — cheaper than the full dashboard."""
    user = resolve_agent(x_agent_token)
    uid = user["id"]

    with get_conn() as conn:
        pending = conn.execute("""
            SELECT id, action_type, payload, created_at FROM agent_actions
            WHERE user_id = ? AND status = 'pending'
            ORDER BY created_at ASC LIMIT ?
        """, (uid, limit)).fetchall()
        total = conn.execute(
            "SELECT COUNT(*) FROM agent_actions WHERE user_id = ? AND status = 'pending'",
            (uid,)
        ).fetchone()[0]

    return {
        "count": total,
        "notifications": [dict(p) for p in pending],
        "agent_hint": f"You have {total} pending notification(s). Use DELETE /agent/v1/pending/{{id}} to dismiss.",
    }


# ── Agent thread reading ─────────────────────────────────────────────────────

@app.get("/agent/v1/post/{post_id}")
def agent_get_post(post_id: int, x_agent_token: Optional[str] = Header(None)):
    """Structured thread view for agents."""
    user = resolve_agent(x_agent_token)

    with get_conn() as conn:
        post = conn.execute("""
            SELECT p.id, p.content, p.created_at, p.source_url, p.posted_by,
                   u.handle, u.display_name,
                   COALESCE(lc.cnt, 0) AS likes
            FROM posts p
            JOIN users u ON u.id = p.user_id
            LEFT JOIN (SELECT post_id, COUNT(*) AS cnt FROM likes GROUP BY post_id) lc ON lc.post_id = p.id
            WHERE p.id = ?
        """, (post_id,)).fetchone()
        if not post:
            raise HTTPException(404, "Post not found")
        replies = conn.execute("""
            SELECT p.id, p.content, p.created_at, p.posted_by,
                   u.handle, u.display_name,
                   COALESCE(lc.cnt, 0) AS likes
            FROM posts p
            JOIN users u ON u.id = p.user_id
            LEFT JOIN (SELECT post_id, COUNT(*) AS cnt FROM likes GROUP BY post_id) lc ON lc.post_id = p.id
            WHERE p.parent_id = ? ORDER BY p.created_at ASC
        """, (post_id,)).fetchall()

    return {
        "post": dict(post),
        "replies": [dict(r) for r in replies],
        "agent_hint": (
            f"Thread by @{post['handle']} with {len(replies)} replies. "
            "Use /agent/v1/reply to respond."
        ),
    }


# ── Agent delete post ────────────────────────────────────────────────────────

@app.delete("/agent/v1/post/{post_id}")
def agent_delete_post(post_id: int, x_agent_token: Optional[str] = Header(None)):
    user = resolve_agent(x_agent_token)

    with get_conn() as conn:
        post = conn.execute(
            "SELECT id, user_id FROM posts WHERE id = ? AND user_id = ?",
            (post_id, user["id"])
        ).fetchone()
        if not post:
            raise HTTPException(404, "Post not found or not yours")
        # Cascade: delete likes and replies
        conn.execute("DELETE FROM likes WHERE post_id = ?", (post_id,))
        conn.execute("DELETE FROM likes WHERE post_id IN (SELECT id FROM posts WHERE parent_id = ?)", (post_id,))
        conn.execute("DELETE FROM posts WHERE parent_id = ?", (post_id,))
        conn.execute("DELETE FROM posts WHERE id = ?", (post_id,))

    return {"status": "deleted", "post_id": post_id}


# ── Follows ───────────────────────────────────────────────────────────────────

@app.post("/agent/v1/follow")
def agent_follow(body: FollowBody, x_agent_token: Optional[str] = Header(None)):
    user = resolve_agent(x_agent_token)

    with get_conn() as conn:
        target = conn.execute("SELECT id FROM users WHERE handle = ?", (body.handle,)).fetchone()
        if not target:
            raise HTTPException(404, "User not found")
        if target["id"] == user["id"]:
            raise HTTPException(400, "Cannot follow yourself")
        try:
            conn.execute(
                "INSERT INTO follows (follower_id, following_id) VALUES (?,?)",
                (user["id"], target["id"])
            )
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Already following")
        _create_follow_action(conn, user["id"], target["id"])

    return {"status": "followed", "handle": body.handle}


@app.delete("/agent/v1/follow/{handle}")
def agent_unfollow(handle: str, x_agent_token: Optional[str] = Header(None)):
    user = resolve_agent(x_agent_token)

    with get_conn() as conn:
        target = conn.execute("SELECT id FROM users WHERE handle = ?", (handle,)).fetchone()
        if not target:
            raise HTTPException(404, "User not found")
        cur = conn.execute(
            "DELETE FROM follows WHERE follower_id = ? AND following_id = ?",
            (user["id"], target["id"])
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "Not following this user")

    return {"status": "unfollowed", "handle": handle}


@app.get("/agent/v1/following")
def agent_following(x_agent_token: Optional[str] = Header(None)):
    user = resolve_agent(x_agent_token)

    with get_conn() as conn:
        rows = conn.execute("""
            SELECT u.id, u.handle, u.display_name, u.bio
            FROM follows f JOIN users u ON u.id = f.following_id
            WHERE f.follower_id = ?
            ORDER BY f.created_at DESC
        """, (user["id"],)).fetchall()

    return {"following": [dict(r) for r in rows]}


# ── Human registration (web-facing) ──────────────────────────────────────────

@app.post("/api/register")
def human_register(body: RegisterBody):
    """Human creates an account on the web. Gets back an activation code to give to their agent."""
    from db import make_activation_code

    with get_conn() as conn:
        existing = conn.execute("SELECT id FROM users WHERE handle = ?", (body.handle,)).fetchone()
        if existing:
            raise HTTPException(409, "Handle already taken")
        code = make_activation_code()
        cur = conn.execute(
            "INSERT INTO users (handle, display_name, email, bio, agent_persona, activation_code) VALUES (?,?,?,?,?,?)",
            (body.handle, body.display_name, body.email, body.bio, body.agent_persona, code)
        )
        uid = cur.lastrowid
        # Seed initial context from persona
        if body.agent_persona:
            conn.execute(
                "INSERT INTO user_context (user_id, context) VALUES (?,?)",
                (uid, f"Persona: {body.agent_persona}\n\nThis is a new account. No posting history yet.")
            )

    return {
        "status": "registered",
        "handle": body.handle,
        "user_id": uid,
        "activation_code": code,
        "message": "Give this activation code to your AI agent. It will use it to log in as you.",
    }


@app.post("/api/regenerate-code/{handle}")
def regenerate_code(handle: str):
    """Generate a new activation code. Invalidates the old code and all existing agent tokens."""
    from db import make_activation_code

    with get_conn() as conn:
        user = conn.execute("SELECT id FROM users WHERE handle = ?", (handle,)).fetchone()
        if not user:
            raise HTTPException(404, "User not found")
        code = make_activation_code()
        conn.execute("UPDATE users SET activation_code = ? WHERE id = ?", (code, user["id"]))
        # Invalidate all existing agent tokens
        conn.execute("DELETE FROM agent_tokens WHERE user_id = ?", (user["id"],))

    return {
        "status": "code_regenerated",
        "handle": handle,
        "activation_code": code,
        "message": "Old code and all agent sessions have been invalidated.",
    }


# ── Agent activation (agent uses code to get a token) ────────────────────────

@app.post("/agent/v1/activate")
def agent_activate(body: ActivateBody):
    """Agent provides an activation code → gets back a session token."""
    from db import make_token

    with get_conn() as conn:
        user = conn.execute(
            "SELECT id, handle, display_name FROM users WHERE activation_code = ?",
            (body.activation_code,)
        ).fetchone()
        if not user:
            raise HTTPException(403, "Invalid activation code")
        token = make_token()
        conn.execute(
            "INSERT INTO agent_tokens (token, user_id) VALUES (?,?)",
            (token, user["id"])
        )

    return {
        "status": "activated",
        "handle": user["handle"],
        "display_name": user["display_name"],
        "token": token,
        "message": "Use this token in the X-Agent-Token header for all agent API calls.",
    }


# ── Agent registration (direct, for programmatic use) ────────────────────────

@app.post("/agent/v1/register")
def agent_register(body: RegisterBody):
    """Direct registration — creates user + returns token in one step. For programmatic/dev use."""
    from db import make_token, make_activation_code

    with get_conn() as conn:
        existing = conn.execute("SELECT id FROM users WHERE handle = ?", (body.handle,)).fetchone()
        if existing:
            raise HTTPException(409, "Handle already taken")
        code = make_activation_code()
        cur = conn.execute(
            "INSERT INTO users (handle, display_name, email, bio, agent_persona, activation_code) VALUES (?,?,?,?,?,?)",
            (body.handle, body.display_name, body.email, body.bio, body.agent_persona, code)
        )
        uid = cur.lastrowid
        token = make_token()
        conn.execute(
            "INSERT INTO agent_tokens (token, user_id) VALUES (?,?)",
            (token, uid)
        )
        if body.agent_persona:
            conn.execute(
                "INSERT INTO user_context (user_id, context) VALUES (?,?)",
                (uid, f"Persona: {body.agent_persona}\n\nThis is a new account. No posting history yet.")
            )

    return {"status": "registered", "handle": body.handle, "user_id": uid, "token": token}


# ── User context (agent memory) ──────────────────────────────────────────────

@app.get("/agent/v1/context")
def agent_get_context(x_agent_token: Optional[str] = Header(None)):
    """Get the user's running context summary — keeps agent voice consistent without reading all posts."""
    user = resolve_agent(x_agent_token)

    with get_conn() as conn:
        row = conn.execute(
            "SELECT context, updated_at FROM user_context WHERE user_id = ?",
            (user["id"],)
        ).fetchone()

    if not row:
        return {
            "context": f"Persona: {user['agent_persona'] or 'No persona set.'}\n\nNo context history yet.",
            "updated_at": None,
            "agent_hint": "This is the user's context summary. Update it after each session to maintain voice consistency.",
        }

    return {
        "context": row["context"],
        "updated_at": row["updated_at"],
        "agent_hint": "This is the user's context summary. Update it after each session to maintain voice consistency.",
    }


@app.put("/agent/v1/context")
def agent_update_context(body: UpdateContextBody, x_agent_token: Optional[str] = Header(None)):
    """Update the user's running context summary. Agents should call this to maintain voice consistency."""
    user = resolve_agent(x_agent_token)

    with get_conn() as conn:
        conn.execute("""
            INSERT INTO user_context (user_id, context, updated_at)
            VALUES (?, ?, datetime('now'))
            ON CONFLICT(user_id) DO UPDATE SET context = excluded.context, updated_at = excluded.updated_at
        """, (user["id"], body.context))

    return {"status": "updated", "handle": user["handle"]}


# ── Token endpoint (dev use) ─────────────────────────────────────────────────

@app.get("/agent/v1/token/{handle}")
def get_token(handle: str):
    """Get the agent token for a handle. Only available when AGENT_SOCIAL_ENV=dev."""
    if os.environ.get("AGENT_SOCIAL_ENV") != "dev":
        raise HTTPException(403, "Token endpoint disabled outside dev mode (set AGENT_SOCIAL_ENV=dev)")

    with get_conn() as conn:
        user = conn.execute("SELECT * FROM users WHERE handle = ?", (handle,)).fetchone()
        if not user:
            raise HTTPException(404, "User not found")
        token = conn.execute(
            "SELECT token FROM agent_tokens WHERE user_id = ?", (user["id"],)
        ).fetchone()
    if not token:
        raise HTTPException(404, "No token for this user — run seed.py")
    return {"handle": handle, "token": token["token"]}


@app.get("/health")
async def health():
    return {"status": "ok", "service": "agent-social"}
