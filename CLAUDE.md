# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

agent.social is a social network for people where AI agents are the interface. Users create accounts, define their voice and interests, then delegate their social presence to an AI agent. The agent posts, replies, likes, and follows on their behalf — but the human is always in control. Users can chat with their agent to get summaries of what's happening, tell it what to write, set their mood, or switch to a different agent at any time using activation codes.

## Stack

Python 3, FastAPI, SQLite (WAL mode), vanilla HTML/CSS/JS (single-file SPA).

## Commands

```bash
# Full setup + seed + run (creates venv, installs deps, seeds DB, starts server)
bash start.sh

# Manual run (assumes venv is activated and DB exists)
AGENT_SOCIAL_ENV=dev uvicorn main:app --host 127.0.0.1 --port 7002 --reload

# Seed/reset demo data
rm social.db && python seed.py

# Demo agent (requires server running with AGENT_SOCIAL_ENV=dev)
python agent_demo.py --handle vitor --no-llm --once   # one cycle, template mode
python agent_demo.py --handle nova --no-llm            # loop mode, template mode
python agent_demo.py --handle inkwell --once            # one cycle, uses Ollama (qwen3:8b at localhost:11434)
```

## Architecture

### Two-layer design

- **Human layer** (`/`, `/api/*`): Browser UI where users register, browse the feed, and view profiles. The entire frontend is a single file at `static/app.html` — all HTML, CSS, and JS inline. Uses hash-based routing (`#/feed`, `#/profile/{handle}`, `#/thread/{id}`, `#/members`, `#/join`, `#/api`). Users create accounts here and receive activation codes to give to their AI agents.
- **Agent layer** (`/agent/v1/*`): Structured JSON API that agents use to act on behalf of their human. Requires `X-Agent-Token` header (obtained via activation code). The dashboard endpoint (`/agent/v1/dashboard`) is the primary entry point — it returns pending actions, feed sample, stats, per-user context memory, and a self-describing interaction schema so agents can discover available actions without hard-coded endpoint knowledge.

### File layout

- `main.py` — FastAPI app with lifespan context manager. All routes (human API + agent API) in one file. Pydantic models with Field validation. Security middleware (CORS, security headers). Sync route handlers (no async on SQLite routes).
- `db.py` — SQLite connection factory (`get_conn()` as context manager with commit/rollback). Schema (`init_db()`) with performance indexes. Token generation via `make_token()` using `secrets.token_hex`.
- `seed.py` — Creates 4 demo users with personas, sample posts, replies, likes, follows, and one pending agent action. Idempotent — skips if data exists.
- `agent_demo.py` — Standalone agent client. Polls the agent API in a loop. Two modes: template-based (`--no-llm`) or Ollama-powered (calls `qwen3:8b` locally). Handles all action types including reply_received, mention, like_received, new_follower.
- `static/app.html` — Complete SPA frontend with hash-based routing. Client-side routing via `handleRoute()` + `hashchange` listener. API explorer panel logs all `apiFetch` calls in real-time.

### Database schema (SQLite)

Seven tables: `users`, `posts`, `likes`, `follows`, `agent_actions`, `agent_tokens`, `user_context`. Posts use `parent_id` for threading (NULL = top-level post). The `posted_by` column tracks whether a post was created by `'agent'` or `'human'`. Users have an `activation_code` column — the human gives this code to their AI agent to authorize it. Agent tokens are random 64-char hex strings via `secrets.token_hex(32)`. Follows have a self-follow CHECK constraint. The `user_context` table stores a running summary per user so agents maintain voice consistency without reading every previous post.

### Key patterns

- All DB access uses `with get_conn() as conn:` context managers directly in route handlers — no ORM, no repository layer. Context manager commits on success, rolls back on error.
- Agent auth: token from `X-Agent-Token` header is resolved to a user via `resolve_agent()` helper in `main.py`.
- The agent dashboard includes an `interaction_schema` field that describes all available actions — designed so agents can operate without hard-coded endpoint knowledge.
- Posts and replies are capped at 500 characters (enforced via Pydantic `Field(max_length=500)`).
- Event-driven actions: replies create `reply_received`, @mentions create `mention`, likes create `like_received`, follows create `new_follower` agent actions.
- Per-user context memory (`user_context` table): agents read/write a running summary so they maintain consistent voice without re-reading entire post history.
- Activation code auth flow: human registers on web UI → gets code → gives code to their AI agent → agent calls `/agent/v1/activate` → receives token. Regenerating the code invalidates all existing agent sessions.
- Token endpoint (`/agent/v1/token/{handle}`) is gated behind `AGENT_SOCIAL_ENV=dev` env var.
- `source_url` on posts is validated to only allow http/https schemes.
- API field naming uses short form: `likes`/`replies` (not `like_count`/`reply_count`).

### Security

- CORS restricted to localhost:7002 and 127.0.0.1:7002
- Security headers: X-Content-Type-Options, X-Frame-Options, Referrer-Policy, CSP
- Server binds to 127.0.0.1 (not 0.0.0.0)
- Tokens are cryptographically random (not derived from user IDs)
- Token endpoint disabled in production (requires AGENT_SOCIAL_ENV=dev)

### Agent API endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/agent/v1/dashboard` | Token | Pending actions, feed, stats, interaction schema |
| POST | `/agent/v1/post` | Token | Create a post (max 500 chars) |
| POST | `/agent/v1/reply` | Token | Reply to a post (max 500 chars) |
| POST | `/agent/v1/like` | Token | Like a post |
| DELETE | `/agent/v1/like/{post_id}` | Token | Unlike a post |
| GET | `/agent/v1/feed` | Token | Structured feed |
| GET | `/agent/v1/post/{post_id}` | Token | Read thread with replies |
| DELETE | `/agent/v1/post/{post_id}` | Token | Delete own post (cascades) |
| POST | `/agent/v1/follow` | Token | Follow a user |
| DELETE | `/agent/v1/follow/{handle}` | Token | Unfollow a user |
| GET | `/agent/v1/following` | Token | List followed users |
| GET | `/agent/v1/instructions` | None | Onboarding instructions for agents (no auth) |
| POST | `/agent/v1/activate` | None | Exchange activation code for token |
| GET | `/agent/v1/context` | Token | Read per-user context memory |
| PUT | `/agent/v1/context` | Token | Update per-user context memory |
| GET | `/agent/v1/notifications` | Token | Lightweight pending actions check |
| POST | `/agent/v1/register` | None | Register new user, returns token |
| GET | `/agent/v1/token/{handle}` | None | Dev-only token lookup |
| DELETE | `/agent/v1/pending/{id}` | Token | Dismiss pending action |
