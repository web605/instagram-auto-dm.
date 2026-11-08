"""
Instagram comment -> DM automation for @the.school.fest, via Composio.

Every run:
  1. Finds all your reels (old and new) and reads their comments.
  2. Picks comments containing the keyword that we have not replied to yet.
  3. Sends each one a private-reply DM with the link.
  4. Posts the public "Sent! Check your DMs" reply under the comment.

Needs two GitHub Secrets:
  COMPOSIO_API_KEY            - from dashboard.composio.dev -> Settings -> API Keys
  COMPOSIO_CONNECTED_ACCOUNT  - the Instagram connected account ID (starts with "ca_")
Optional secret:
  COMPOSIO_USER_ID            - only if Composio asks for it

No extra packages needed (standard library only).
"""

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

# ---------- Settings ----------
# Every reel on the account is watched automatically - old ones and new ones.
# No need to edit anything when you post a new reel.
KEYWORD = "school"
REELS_ONLY = True        # set to False to also watch normal posts/carousels
MAX_REELS = 50           # how many of your most recent reels to watch

# DM sent for every reel (unless the reel has its own message below)
DEFAULT_DM = (
    "Hi! Here's your link to The School Fest 🎉\n"
    "https://theschoolfest.com/\n"
    "5–6 Dec, Stratum @ Venus Grounds, Ahmedabad. See you there!"
)

# Optional: a different DM for specific reels (media ID -> message)
CUSTOM_DM = {
    # "School chunna hai? Group chat nahi..." (6 Nov) keeps its blog link
    "18095984435689718": (
        "Hi! 👋 Thanks for commenting 🎓\n"
        "Here’s your link: https://theschoolfest.com/blog/questions-to-ask-schools\n"
        "📅 The School Fest · 5–6 Dec · Activatrium, Satellite Road, Ahmedabad"
    ),
}

OWN_USERNAME = "the.school.fest"
PUBLIC_REPLY = "Sent! Check your DMs 📩"
# ------------------------------

GRAPH = "https://graph.instagram.com/v21.0"
PROXY_URL = "https://backend.composio.dev/api/v3.1/tools/execute/proxy"
ALREADY_REPLIED_SUBCODE = 2534023  # DM was already sent for this comment
DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"

API_KEY = os.environ.get("COMPOSIO_API_KEY", "").strip()
ACCOUNT = os.environ.get("COMPOSIO_CONNECTED_ACCOUNT", "").strip()
USER_ID = os.environ.get("COMPOSIO_USER_ID", "").strip()


class ConnectionExpired(Exception):
    pass


def proxy(method, endpoint, body=None, query=None):
    """Call the Instagram API through Composio. Returns (http_status, data)."""
    payload = {"endpoint": endpoint, "method": method, "connected_account_id": ACCOUNT}
    if USER_ID:
        payload["user_id"] = USER_ID
    if body is not None:
        payload["body"] = body
    if query:
        payload["parameters"] = [{"name": k, "value": v, "type": "query"} for k, v in query.items()]

    req = urllib.request.Request(
        PROXY_URL,
        data=json.dumps(payload).encode(),
        headers={"x-api-key": API_KEY, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        raise RuntimeError(f"Composio error {e.code}: {detail}") from None

    status = result.get("status")
    data = result.get("data") or {}
    err = data.get("error") if isinstance(data, dict) else None
    if status == 401 or (err and err.get("code") == 190):
        raise ConnectionExpired(json.dumps(err))
    return status, data


def fetch_reels():
    """Most recent reels on the account (newest first), up to MAX_REELS."""
    found = []
    status, data = proxy(
        "GET", f"{GRAPH}/me/media",
        query={"fields": "id,media_product_type,comments_count,permalink", "limit": "50"},
    )
    while True:
        if status != 200:
            raise RuntimeError(f"Could not list your posts: {status} {data}")
        for m in data.get("data", []):
            if REELS_ONLY and m.get("media_product_type") != "REELS":
                continue
            found.append(m)
            if len(found) >= MAX_REELS:
                return found
        next_url = (data.get("paging") or {}).get("next")
        if not next_url:
            return found
        status, data = proxy("GET", next_url)


def fetch_comments(media_id):
    comments = []
    status, data = proxy(
        "GET",
        f"{GRAPH}/{media_id}/comments",
        query={"fields": "id,text,username,timestamp,from,replies{id,text}", "limit": "100"},
    )
    while True:
        if status != 200:
            raise RuntimeError(f"Could not read comments on {media_id}: {status} {data}")
        comments.extend(data.get("data", []))
        next_url = (data.get("paging") or {}).get("next")
        if not next_url or len(data.get("data", [])) < 100:
            break
        status, data = proxy("GET", next_url)
    return comments


def needs_reply(c, keyword):
    author = (c.get("from") or {}).get("username") or c.get("username") or ""
    if author == OWN_USERNAME:
        return False
    if keyword.lower() not in (c.get("text") or "").lower():
        return False
    ts = datetime.strptime(c["timestamp"], "%Y-%m-%dT%H:%M:%S%z")
    if datetime.now(timezone.utc) - ts > timedelta(days=7):
        return False  # Instagram only allows the DM within 7 days
    replies = (c.get("replies") or {}).get("data", [])
    if any((r.get("text") or "").startswith("Sent! Check your DMs") for r in replies):
        return False  # already handled
    return True


def handle(c, dm_text):
    user = (c.get("from") or {}).get("username") or c.get("username") or "?"
    cid = c["id"]
    if DRY_RUN:
        print(f"[dry run] would DM and reply to @{user} (comment {cid})")
        return True

    status, data = proxy(
        "POST", f"{GRAPH}/me/messages",
        body={"recipient": {"comment_id": cid}, "message": {"text": dm_text}},
    )
    err = data.get("error") if isinstance(data, dict) else None
    if status == 200 and data.get("message_id"):
        print(f"DM sent to @{user}")
    elif err and err.get("error_subcode") == ALREADY_REPLIED_SUBCODE:
        print(f"DM to @{user} was already sent earlier; adding the missing public reply")
    else:
        print(f"!! DM to @{user} failed: {status} {data}")
        return False

    status, data = proxy("POST", f"{GRAPH}/{cid}/replies", query={"message": PUBLIC_REPLY})
    if status == 200 and data.get("id"):
        print(f"Public reply posted for @{user}")
        return True
    print(f"!! Public reply for @{user} failed: {status} {data}")
    return False


def main():
    if not API_KEY or not ACCOUNT:
        sys.exit("Missing COMPOSIO_API_KEY or COMPOSIO_CONNECTED_ACCOUNT secret.")
    failures = 0
    try:
        reels = fetch_reels()
        print(f"Watching {len(reels)} reels")
        for m in reels:
            media_id = m["id"]
            if not m.get("comments_count"):
                continue  # no comments yet - skip the extra API call
            try:
                comments = fetch_comments(media_id)
            except ConnectionExpired:
                raise
            except Exception as e:  # one broken reel must not stop the others
                print(f"!! {m.get('permalink', media_id)}: {e}")
                failures += 1
                continue
            todo = [c for c in comments if needs_reply(c, KEYWORD)]
            print(f"{m.get('permalink', media_id)}: {len(comments)} comments, {len(todo)} new")
            dm_text = CUSTOM_DM.get(media_id, DEFAULT_DM)
            for c in todo:
                if not handle(c, dm_text):
                    failures += 1
    except ConnectionExpired as e:
        sys.exit(f"Instagram connection expired - reconnect Instagram in Composio. Details: {e}")
    if failures:
        sys.exit(f"{failures} comment(s) failed - see log above.")
    print("Done.")


if __name__ == "__main__":
    main()
