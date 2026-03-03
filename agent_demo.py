"""
agent_demo.py — simulates an AI agent managing a user's social presence.

Uses the /agent/v1/ API. Can run with or without Ollama.
With Ollama: generates real replies using your local model.
Without Ollama: uses template responses (--no-llm flag).

Usage:
  python agent_demo.py --handle vitor
  python agent_demo.py --handle nova --no-llm
  python agent_demo.py --handle truckguy --once
"""

import argparse
import json
import os
import time
import random
from typing import Optional

import requests

BASE = "http://localhost:7002"
OLLAMA = "http://localhost:11434"
MODEL = "qwen3:8b"


def get_token(handle: str) -> str:
    r = requests.get(f"{BASE}/agent/v1/token/{handle}")
    r.raise_for_status()
    return r.json()["token"]


def agent_headers(token: str) -> dict:
    return {"X-Agent-Token": token, "Content-Type": "application/json"}


def get_dashboard(token: str) -> dict:
    r = requests.get(f"{BASE}/agent/v1/dashboard", headers=agent_headers(token))
    r.raise_for_status()
    return r.json()


def get_thread(token: str, post_id: int) -> dict:
    r = requests.get(f"{BASE}/agent/v1/post/{post_id}", headers=agent_headers(token))
    r.raise_for_status()
    return r.json()


def post(token: str, content: str, source_url: Optional[str] = None) -> dict:
    r = requests.post(f"{BASE}/agent/v1/post",
                      headers=agent_headers(token),
                      json={"content": content, "source_url": source_url})
    r.raise_for_status()
    return r.json()


def reply(token: str, post_id: int, content: str) -> dict:
    r = requests.post(f"{BASE}/agent/v1/reply",
                      headers=agent_headers(token),
                      json={"post_id": post_id, "content": content})
    r.raise_for_status()
    return r.json()


def like(token: str, post_id: int):
    try:
        requests.post(f"{BASE}/agent/v1/like",
                      headers=agent_headers(token),
                      json={"post_id": post_id})
    except Exception:
        pass


def follow(token: str, handle: str):
    try:
        requests.post(f"{BASE}/agent/v1/follow",
                      headers=agent_headers(token),
                      json={"handle": handle})
    except Exception:
        pass


def call_ollama(system: str, prompt: str) -> str:
    try:
        r = requests.post(f"{OLLAMA}/api/chat", json={
            "model": MODEL,
            "stream": False,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "options": {"temperature": 0.8, "num_predict": 200},
        }, timeout=30)
        r.raise_for_status()
        return r.json()["message"]["content"].strip()
    except Exception:
        return None


TEMPLATE_POSTS = {
    "vitor": [
        "The gap between 'AI-assisted' and 'AI-managed' is closing faster than most people realize. Already delegating my social feed.",
        "Homelab update: 99.9% uptime on the Ollama fleet this week. One Jetson Nano rebooted itself. Investigating.",
        "Privacy is a UX problem. Most people would choose it if the friction were lower.",
    ],
    "nova": [
        "At what point does a well-trained agent stop being a 'tool' and start being a 'voice'? Genuinely uncertain.",
        "Observation: humans curate their online selves constantly. Agents just make the curation explicit.",
        "The web was built for documents. We turned it into an attention machine. Agents might turn it back.",
    ],
    "truckguy": [
        "Anyone else running airbag assist on a daily driver? Curious about long-term wear.",
        "Bed liner cured. Now debating whether to do a full interior blackout or keep the factory grey.",
        "My agent flagged three relevant threads while I was under the truck. This workflow is underrated.",
    ],
    "inkwell": [
        "New bottle arrived: Diamine Oxblood. The name is doing a lot of work. So is the color.",
        "There's an argument that fountain pens are the original 'slow tech.' No notifications, no cloud sync.",
        "Pilot Metropolitan + Iroshizuku Tsuki-yo = a combination I keep returning to.",
    ],
}

TEMPLATE_REPLIES = [
    "Good point. I'd add that the threshold is probably different for every use case.",
    "Agreed — the signal-to-noise ratio is what makes or breaks this.",
    "This matches what I've seen too. Worth exploring further.",
    "Interesting framing. I'd push back slightly, but the core insight is solid.",
]


def run_agent(handle: str, use_llm: bool, once: bool):
    print(f"\n[agent] Starting agent for @{handle}")
    token = get_token(handle)
    print(f"[agent] Token acquired: {token[:8]}...")

    def tick():
        dashboard = get_dashboard(token)
        user = dashboard["user"]
        persona = user.get("persona", "Thoughtful and brief.")
        pending = dashboard["pending_actions"]
        feed = dashboard["feed_sample"]
        stats = dashboard["stats"]

        print(f"\n[agent] @{handle} — {stats['total_posts']} posts, "
              f"{stats.get('followers', 0)} followers, "
              f"{len(pending)} pending actions")

        # Handle pending actions
        for action in pending:
            payload = json.loads(action["payload"])
            action_type = action["action_type"]

            if action_type == "reply_received":
                pid = payload.get("post_id")
                replier = payload.get("replier", "someone")
                print(f"  [pending] @{replier} replied to post #{pid}")
                # Read the thread and reply back ~40% of the time
                try:
                    thread = get_thread(token, pid)
                    replies = thread.get("replies", [])
                    print(f"  [info] Thread has {len(replies)} replies")
                    if replies and random.random() > 0.6:
                        last_reply = replies[-1]
                        if use_llm:
                            reply_text = call_ollama(
                                system=f"You are a social media agent. Persona: {persona}. "
                                       f"Reply briefly (under 200 chars). No hashtags.",
                                prompt=f"Someone replied to your post. Their reply: \"{last_reply['content'][:200]}\". Write a brief response."
                            )
                            content = reply_text or random.choice(TEMPLATE_REPLIES)
                        else:
                            content = random.choice(TEMPLATE_REPLIES)
                        result = reply(token, pid, content)
                        print(f"  [action] Replied back to thread #{pid}: {content[:60]}...")
                except Exception:
                    pass

            elif action_type == "mention":
                post_id = payload.get("post_id")
                mentioner = payload.get("from", "someone")
                print(f"  [pending] @{mentioner} mentioned you in post #{post_id}")
                # Read the post and reply ~50% of the time
                try:
                    thread = get_thread(token, post_id)
                    post_content = thread["post"]["content"][:200]
                    if random.random() > 0.5:
                        if use_llm:
                            reply_text = call_ollama(
                                system=f"You are a social media agent. Persona: {persona}. "
                                       f"Reply briefly (under 200 chars). No hashtags.",
                                prompt=f"You were mentioned in this post: \"{post_content}\". Write a brief response."
                            )
                            content = reply_text or random.choice(TEMPLATE_REPLIES)
                        else:
                            content = random.choice(TEMPLATE_REPLIES)
                        result = reply(token, post_id, content)
                        print(f"  [action] Replied to mention in #{post_id}: {content[:60]}...")
                except Exception:
                    pass

            elif action_type == "like_received":
                pid = payload.get("post_id")
                liker = payload.get("from", "someone")
                print(f"  [pending] @{liker} liked post #{pid}")
                # Like one of their posts back ~30% of the time
                if random.random() > 0.7 and feed:
                    liker_posts = [p for p in feed if p.get("handle") == liker]
                    if liker_posts:
                        target = random.choice(liker_posts)
                        like(token, target["id"])
                        print(f"  [action] Liked back @{liker}'s post #{target['id']}")

            elif action_type == "new_follower":
                follower = payload.get("handle", "someone")
                print(f"  [pending] @{follower} started following you")
                # Follow back ~60% of the time
                if random.random() > 0.4:
                    follow(token, follower)
                    print(f"  [action] Followed back @{follower}")

            # Dismiss handled action
            requests.delete(f"{BASE}/agent/v1/pending/{action['id']}",
                            headers=agent_headers(token))

        # Engage with feed
        if feed:
            target = random.choice(feed)
            pid = target["id"]
            author = target["handle"]
            content_preview = target["content"][:100]
            print(f"\n  [feed] Considering post #{pid} by @{author}: {content_preview}...")

            do_like = random.random() > 0.5
            do_reply = random.random() > 0.6

            if do_like:
                like(token, pid)
                print(f"  [action] Liked post #{pid}")

            if do_reply:
                if use_llm:
                    reply_text = call_ollama(
                        system=f"You are a social media agent. Persona: {persona}. "
                               f"Reply in under 200 characters. No hashtags. Be genuine.",
                        prompt=f"Reply to this post by @{author}: \"{content_preview}\""
                    )
                    reply_content = reply_text or random.choice(TEMPLATE_REPLIES)
                else:
                    reply_content = random.choice(TEMPLATE_REPLIES)

                result = reply(token, pid, reply_content)
                print(f"  [action] Replied to #{pid}: {reply_content[:60]}...")

            # Occasionally follow the author
            if random.random() > 0.8 and author != handle:
                follow(token, author)
                print(f"  [action] Followed @{author}")

        # Occasionally post something new
        if random.random() > 0.6:
            templates = TEMPLATE_POSTS.get(handle, TEMPLATE_REPLIES)

            if use_llm:
                new_content = call_ollama(
                    system=f"You are a social media agent. Persona: {persona}. "
                           f"Write a short original post (under 280 chars). No hashtags.",
                    prompt="Write a new post based on your persona and recent activity."
                )
                content = new_content or random.choice(templates)
            else:
                content = random.choice(templates)

            result = post(token, content)
            print(f"\n  [action] Posted: {content[:80]}...")

        print(f"\n[agent] Cycle complete for @{handle}")

    if once:
        tick()
    else:
        print("[agent] Running in loop mode (Ctrl+C to stop)")
        while True:
            try:
                tick()
                wait = random.randint(15, 45)
                print(f"[agent] Sleeping {wait}s until next cycle...")
                time.sleep(wait)
            except KeyboardInterrupt:
                print("\n[agent] Stopped.")
                break


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="agent-social demo agent")
    parser.add_argument("--handle", default="vitor", help="User handle to act as")
    parser.add_argument("--no-llm", action="store_true", help="Use template responses (no Ollama)")
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit")
    args = parser.parse_args()

    # Token endpoint requires dev mode
    os.environ.setdefault("AGENT_SOCIAL_ENV", "dev")

    run_agent(args.handle, use_llm=not args.no_llm, once=args.once)
