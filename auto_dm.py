"""
Instagram comment + DM auto-replies for @the.school.fest, via Composio.

Every check:
  1. Reads comments on your reels and posts (last 7 days) and new DMs (last 24 hours).
  2. Works out what each person wants (registration, date, venue, price, partnering...).
  3. Replies with the matching message - DM + public "Sent!" reply for comments.
  4. Anything it doesn't understand is NOT answered - it opens a GitHub Issue
     instead, and GitHub emails you about it.

Secrets: COMPOSIO_API_KEY, COMPOSIO_CONNECTED_ACCOUNT (optional COMPOSIO_USER_ID).
GITHUB_TOKEN / GITHUB_REPOSITORY are provided by GitHub Actions (used for flags).
No extra packages needed (standard library only).
"""

import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

# =================== Event facts (edit here) ===================
LINK = "https://theschoolfest.com/"
WHEN = "5–6 Dec (Sat–Sun) · 10 AM–7 PM"
WHERE = "Activatrium, Level 4, Stratum @ Venus Grounds, Satellite Road, Ahmedabad"
DETAILS = f"📅 {WHEN}\n📍 {WHERE}"

# =================== Messages per intent =======================
DM = {
    "register": (
        "Hi! 👋 Here's your free registration link for The School Fest 🎓\n"
        f"{LINK}\n\n{DETAILS}\nSee you there!"
    ),
    "price": (
        "Hi! 👋 Entry to The School Fest is completely FREE 🎉\n"
        f"Just register here: {LINK}\n\n{DETAILS}"
    ),
    "date": (
        f"Hi! 👋 The School Fest is on {WHEN} 📅\n"
        f"📍 {WHERE}\n\nRegister free: {LINK}"
    ),
    "venue": (
        f"Hi! 👋 The School Fest is at 📍 {WHERE}\n"
        f"📅 {WHEN}\n\nRegister free: {LINK}"
    ),
    "greeting": (
        "Hi! 👋 Welcome to The School Fest 🎓 How can we help you today?\n\n"
        "Meet Ahmedabad's top schools, all in one place.\n"
        f"Registration is free 👉 {LINK}\n\n{DETAILS}"
    ),
    "praise": (
        "Thank you so much! 💛\n"
        f"Register free for The School Fest: {LINK}\n\n{DETAILS}"
    ),
    "partner_brand": (
        "Thank you for showing interest in being a part of our School Fest!\n\n"
        "We would love to explore this opportunity with you. Could you please share your "
        "contact details and the best time to connect? This will help us take the "
        "conversation forward and share the relevant details with you.\n\n"
        "Looking forward to connecting with you!"
    ),
    "partner_school": (
        "Thank you so much for showing interest in participating in our School Fest! "
        "We’re really happy to hear from you. 😊\n\n"
        "Could you please share the school’s name along with your contact details? Our team "
        "will get in touch with you and share all the relevant information about "
        "participation and the next steps.\n\n"
        "Looking forward to connecting with you!"
    ),
}

# Reels that send their own blog link when someone asks for the link / comments "school"
CUSTOM_DM = {
    "18095984435689718": (
        "Hi! 👋 Thanks for commenting 🎓\n"
        "Here’s your link: https://theschoolfest.com/blog/questions-to-ask-schools\n"
        "📅 The School Fest · 5–6 Dec · Activatrium, Satellite Road, Ahmedabad"
    ),
    "17965761393237970": (
        "Hi! 👋 Thanks for your interest 🎓\n"
        "Here’s your link: theschoolfest.com/blog/school-guide\n"
        "Meet Ahmedabad’s top schools at The School Fest · 5–6 Dec, Stratum @ Venus Grounds. See you there!"
    ),
}

PUBLIC_REPLY = "Sent! Check your DMs 📩"
PRAISE_REPLY = "Thank you! 💛 See you at The School Fest 🎓"
CLOSED_DM_REPLY = "Hi! Register via the link in our bio 🎓"
HANDLED_PREFIXES = ("Sent! Check your DMs", "Hi! Register via the link in our bio", "Thank you! 💛")

# =================== How intent is detected ====================
# Checked top to bottom; the first group that matches wins.
INTENT_RULES = [
    ("flag", r"scam|fake|fraud|worst|complain|refund|cheat|not working|didn'?t get|did not get|not received|organi[sz]|which company|who is|which schools|schools? (are )?coming|list of schools|kaun ?s[ae]? school|will .{1,30} be there|admission|promot|advertis|our page|paid collab"),
    ("partner_school", r"participat|as a school|our school|my school|school owner|principal|trustee"),
    ("partner_brand", r"stall|booth|display|our products?|brand|sponsor|collab|exhibit|vendor|partner"),
    ("price", r"price|fees?|cost|free|ticket|charges?|paid|kitn[ae]|entry|₹|\brs\b|rupees?"),
    ("date", r"\bdate|when|\bkab\b|timing|\btime|\bdays?\b|december|\bdec\b"),
    ("venue", r"venue|where|kaha+n?|location|address|\bplace|map|jagah"),
    ("register", r"regist|link|sign ?up|\bbook|\bpass\b|join|details|info|interested|\bschool|\bsend|\bshare|\bdm\b|how to"),
    ("greeting", r"^\W*(hi+|hello+|hey+|helo|hlo|namaste|namaskar|hii+)\b"),
    ("praise", r"love|great|nice|awesome|amazing|wow|looking forward|excited|super|good|congrat|best|thank|👏|🔥|❤|😍|🥳|🙌"),
]

OWN_USERNAME = "the.school.fest"
WATCH_FEED_POSTS = True   # also watch normal posts/carousels, not just reels
MAX_MEDIA = 50
COMMENT_DAYS = 7          # Instagram allows the comment DM within 7 days
DM_WINDOW_HOURS = 23      # Instagram allows DM replies within 24 hours
DM_FLAG_DAYS = 3          # flag unanswered DMs we couldn't handle, up to this old
GITHUB_USER = "web605"    # gets the email for each flag

GRAPH = "https://graph.instagram.com/v21.0"
PROXY_URL = "https://backend.composio.dev/api/v3.1/tools/execute/proxy"
ALREADY_REPLIED_SUBCODE = 2534023
DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"

API_KEY = os.environ.get("COMPOSIO_API_KEY", "").strip()
ACCOUNT = os.environ.get("COMPOSIO_CONNECTED_ACCOUNT", "").strip()
USER_ID = os.environ.get("COMPOSIO_USER_ID", "").strip()
GH_TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()
GH_REPO = os.environ.get("GITHUB_REPOSITORY", "").strip()

NOW = datetime.now(timezone.utc)


class ConnectionExpired(Exception):
    pass


# ----------------------------------------------------------------- helpers
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
        PROXY_URL, data=json.dumps(payload).encode(),
        headers={"x-api-key": API_KEY, "Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Composio error {e.code}: {e.read().decode(errors='replace')}") from None
    status = result.get("status")
    data = result.get("data") or {}
    err = data.get("error") if isinstance(data, dict) else None
    if status == 401 or (err and err.get("code") == 190):
        raise ConnectionExpired(json.dumps(err))
    return status, data


def parse_ts(ts):
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S%z")


def detect_intent(text):
    t = (text or "").strip().lower()
    if not t:
        return "flag"
    if not re.sub(r"[\W_]+", "", t):     # only emoji / punctuation
        return "praise"
    for intent, pattern in INTENT_RULES:
        if re.search(pattern, t):
            return intent
    return "flag"


def account_dm_access_off(err):
    return "account owner has disabled access" in ((err or {}).get("message") or "").lower()


# ----------------------------------------------------------------- flags
_flag_keys = None


def flag(key, title, body):
    """Open a GitHub Issue (GitHub emails you). Each key is flagged only once."""
    print(f"🚩 FLAG: {title}")
    try:
        _open_issue(key, title, body)
    except Exception as e:  # a flag problem must never stop the replies
        print(f"!! could not open flag issue: {e}")


def _open_issue(key, title, body):
    global _flag_keys
    if DRY_RUN or not GH_TOKEN or not GH_REPO:
        return
    api = f"https://api.github.com/repos/{GH_REPO}/issues"
    headers = {"Authorization": f"Bearer {GH_TOKEN}", "Accept": "application/vnd.github+json"}
    if _flag_keys is None:
        _flag_keys = set()
        for page in (1, 2, 3):
            req = urllib.request.Request(f"{api}?state=all&per_page=100&page={page}", headers=headers)
            with urllib.request.urlopen(req, timeout=30) as r:
                items = json.loads(r.read().decode())
            _flag_keys.update(re.findall(r"flag-key: (\S+)", " ".join((i.get("body") or "") for i in items)))
            if len(items) < 100:
                break
    if key in _flag_keys:
        return
    full = f"{body}\n\n@{GITHUB_USER} please check this one.\n\n<!-- flag-key: {key} -->"
    req = urllib.request.Request(
        api, data=json.dumps({"title": title[:120], "body": full}).encode(),
        headers={**headers, "Content-Type": "application/json"}, method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=30).read()
        _flag_keys.add(key)
    except urllib.error.HTTPError as e:
        print(f"!! could not open flag issue: {e.code} {e.read().decode(errors='replace')[:200]}")


# ----------------------------------------------------------------- comments
def fetch_media():
    found = []
    status, data = proxy("GET", f"{GRAPH}/me/media",
                         query={"fields": "id,media_product_type,comments_count,permalink", "limit": "50"})
    while True:
        if status != 200:
            raise RuntimeError(f"Could not list your posts: {status} {data}")
        for m in data.get("data", []):
            if not WATCH_FEED_POSTS and m.get("media_product_type") != "REELS":
                continue
            found.append(m)
            if len(found) >= MAX_MEDIA:
                return found
        next_url = (data.get("paging") or {}).get("next")
        if not next_url:
            return found
        status, data = proxy("GET", next_url)


def fetch_comments(media_id):
    comments = []
    status, data = proxy("GET", f"{GRAPH}/{media_id}/comments",
                         query={"fields": "id,text,username,timestamp,from,replies{id,text}", "limit": "100"})
    while True:
        if status != 200:
            raise RuntimeError(f"Could not read comments on {media_id}: {status} {data}")
        comments.extend(data.get("data", []))
        next_url = (data.get("paging") or {}).get("next")
        if not next_url or len(data.get("data", [])) < 100:
            break
        status, data = proxy("GET", next_url)
    return comments


def comment_is_new(c):
    author = (c.get("from") or {}).get("username") or c.get("username") or ""
    if author == OWN_USERNAME:
        return False
    if NOW - parse_ts(c["timestamp"]) > timedelta(days=COMMENT_DAYS):
        return False
    replies = (c.get("replies") or {}).get("data", [])
    # "in" (not startswith) so hand-typed replies like "@name Sent! Check your DMs" count too
    return not any(p in (r.get("text") or "") for r in replies for p in HANDLED_PREFIXES)


def post_reply(cid, user, text):
    status, data = proxy("POST", f"{GRAPH}/{cid}/replies", query={"message": text})
    if status == 200 and data.get("id"):
        print(f"   public reply posted for @{user}")
        return True
    print(f"!! public reply for @{user} failed: {status} {data}")
    return False


def handle_comment(c, media):
    user = (c.get("from") or {}).get("username") or c.get("username") or "?"
    text = c.get("text") or ""
    intent = detect_intent(text)
    link = media.get("permalink", media["id"])
    print(f"   @{user}: {text[:60]!r} -> {intent}")

    if intent == "flag":
        flag(f"comment-{c['id']}", f"🚩 Comment from @{user}: {text[:60]}",
             f"**Comment:** {text}\n**From:** @{user}\n**Post:** {link}\n\n"
             "The bot didn't know how to answer this, so it didn't reply. Please reply yourself in the app.")
        return True
    if intent == "praise" and not text_has_question(text):
        if DRY_RUN:
            print(f"   [dry run] would reply publicly: {PRAISE_REPLY}")
            return True
        return post_reply(c["id"], user, PRAISE_REPLY)

    if intent in ("register", "greeting") and media["id"] in CUSTOM_DM:
        dm_text = CUSTOM_DM[media["id"]]
    else:
        dm_text = DM.get(intent, DM["register"])
    if DRY_RUN:
        print(f"   [dry run] would DM ({intent}) + reply '{PUBLIC_REPLY}'")
        return True

    status, data = proxy("POST", f"{GRAPH}/me/messages",
                         body={"recipient": {"comment_id": c["id"]}, "message": {"text": dm_text}})
    err = data.get("error") if isinstance(data, dict) else None
    if status == 200 and data.get("message_id"):
        print(f"   DM sent to @{user}")
    elif err and err.get("error_subcode") == ALREADY_REPLIED_SUBCODE:
        print(f"   DM to @{user} was already sent earlier; adding the public reply")
    elif account_dm_access_off(err):
        flag("dm-access-off", "🚨 Instagram DM access is OFF - no DMs are going out",
             "Instagram says: *The account owner has disabled access to Instagram Direct Messaging.*\n\n"
             "Fix in the Instagram app: Settings → Messages and story replies → Message controls → "
             "Connected tools → turn on **Allow access to messages**.")
        return False
    elif status == 403 or (err and err.get("code") in (200, 10, 551)):
        print(f"   @{user} can't receive DMs ({(err or {}).get('message', status)}); replying 'link in bio'")
        return post_reply(c["id"], user, CLOSED_DM_REPLY)
    else:
        print(f"!! DM to @{user} failed: {status} {data}")
        return False
    if intent in ("partner_brand", "partner_school"):
        flag(f"comment-{c['id']}", f"🤝 Lead from @{user} (comment): {text[:60]}",
             f"**Comment:** {text}\n**From:** @{user}\n**Post:** {link}\n\n"
             "The bot sent them the partnership message in DMs. Please follow up.")
    return post_reply(c["id"], user, PUBLIC_REPLY)


def text_has_question(text):
    return "?" in text


# ----------------------------------------------------------------- DMs
def handle_dms(own_id):
    status, data = proxy("GET", f"{GRAPH}/me/conversations", query={
        "platform": "instagram", "limit": "25",
        "fields": "id,updated_time,messages.limit(6){id,from,message,created_time}"})
    if status != 200:
        err = data.get("error") if isinstance(data, dict) else None
        if account_dm_access_off(err):
            flag("dm-access-off", "🚨 Instagram DM access is OFF - no DMs are going out",
                 "Fix in the Instagram app: Settings → Messages and story replies → Message controls → "
                 "Connected tools → turn on **Allow access to messages**.")
            return
        raise RuntimeError(f"Could not read DMs: {status} {data}")

    for conv in data.get("data", []):
        if NOW - parse_ts(conv["updated_time"]) > timedelta(days=DM_FLAG_DAYS):
            continue
        msgs = (conv.get("messages") or {}).get("data", [])  # newest first
        if not msgs:
            continue
        last = msgs[0]
        if last["from"].get("id") == own_id:
            continue  # we already answered
        text = (last.get("message") or "").strip()
        if not text:
            continue  # shared post / story mention / photo - nothing to answer
        user = last["from"].get("username", "?")
        age = NOW - parse_ts(last["created_time"])
        we_talked = any(m["from"].get("id") == own_id for m in msgs[1:])
        intent = detect_intent(text)
        print(f"DM @{user}: {text[:60]!r} -> {intent}")

        if we_talked and intent in ("praise", "greeting"):
            continue  # "thanks" / "ok" after our reply - nothing to add
        if intent == "flag" or (we_talked and intent not in ("price", "date", "venue", "register")):
            flag(f"dm-{last['id']}", f"🚩 DM from @{user}: {text[:60]}",
                 f"**Message:** {text}\n**From:** @{user}\n\n"
                 "The bot didn't know how to answer this, so it didn't reply. Please reply in the Instagram app.")
            continue
        if age > timedelta(hours=DM_WINDOW_HOURS):
            flag(f"dm-{last['id']}", f"⏰ DM from @{user} is too old to auto-answer: {text[:50]}",
                 f"**Message:** {text}\n**From:** @{user}\n\nInstagram only allows replies within 24 hours.")
            continue
        if DRY_RUN:
            print(f"   [dry run] would DM ({intent})")
            continue
        st, res = proxy("POST", f"{GRAPH}/me/messages",
                        body={"recipient": {"id": last["from"]["id"]}, "message": {"text": DM.get(intent, DM["register"])}})
        if st == 200 and res.get("message_id"):
            print(f"   DM reply sent to @{user}")
            if intent in ("partner_brand", "partner_school"):
                flag(f"dm-{last['id']}", f"🤝 Lead from @{user} (DM): {text[:60]}",
                     f"**Message:** {text}\n**From:** @{user}\n\n"
                     "The bot sent them the partnership message. Please follow up.")
        else:
            print(f"!! DM reply to @{user} failed: {st} {res}")
            err = res.get("error") if isinstance(res, dict) else None
            if err and (err.get("error_subcode") == 2534022 or err.get("code") == 10):
                # usually an unaccepted message request - Instagram won't let the bot answer it
                flag(f"dm-{last['id']}", f"📥 DM from @{user} needs your reply: {text[:50]}",
                     f"**Message:** {text}\n**From:** @{user}\n\n"
                     "Instagram didn't let the bot reply (usually because it's still a message request). "
                     "Please accept it and reply in the app.")


# ----------------------------------------------------------------- main
def main():
    if not API_KEY or not ACCOUNT:
        sys.exit("Missing COMPOSIO_API_KEY or COMPOSIO_CONNECTED_ACCOUNT secret.")
    failures = 0
    try:
        st, me = proxy("GET", f"{GRAPH}/me", query={"fields": "user_id,username"})
        own_id = me.get("user_id")  # the id DMs show as our sender id
        media = fetch_media()
        print(f"Watching {len(media)} posts and reels")
        for m in media:
            if not m.get("comments_count"):
                continue
            try:
                comments = fetch_comments(m["id"])
            except ConnectionExpired:
                raise
            except Exception as e:
                print(f"!! {m.get('permalink', m['id'])}: {e}")
                failures += 1
                continue
            todo = [c for c in comments if comment_is_new(c)]
            if todo:
                print(f"{m.get('permalink', m['id'])}: {len(todo)} new")
            for c in todo:
                if not handle_comment(c, m):
                    print("   (skipped; will retry next check)")
        try:
            handle_dms(own_id)
        except ConnectionExpired:
            raise
        except Exception as e:
            print(f"!! DMs: {e}")
            failures += 1
    except ConnectionExpired as e:
        sys.exit(f"Instagram connection expired - reconnect Instagram in Composio. Details: {e}")
    if failures:
        sys.exit(f"{failures} problem(s) - see log above.")
    print("Done.")


if __name__ == "__main__":
    main()
