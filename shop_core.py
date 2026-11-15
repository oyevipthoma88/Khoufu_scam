"""Shared pieces of the shop (state, formatting, sending, prices)."""
import asyncio
import logging
import re
import time

from pyrogram.errors import FloodWait
from pyrogram.types import CallbackQuery, Message

import config
import database as db
import logchan
import ui
from logchan import NOPREV
from ui import B, G, P, R, D, build, safe_send

log = logging.getLogger("shop")

BOT = None
SSTATE: dict[int, dict] = {}     # per-user conversation state (owners and buyers)

KIND_ICON = {"account": "📱", "channel": "📢", "group": "👥", "bot": "🤖", "method": "📚"}
TAGS = {"normal": "✅ Normal", "scam": "⚠️ Scam Tag", "fake": "🎭 Fake Tag"}
TAG_KEY = {"normal": "t_normal", "scam": "t_scam", "fake": "t_fake"}


def tag_query(tag: str) -> dict:
    """Old stock has no tag -> counts as normal."""
    return {"tag": {"$nin": ["scam", "fake"]}} if tag == "normal" else {"tag": tag}


def split_ref(ref: str) -> tuple[str, str]:
    """'scam-IN' -> ('scam', 'IN'); old 'IN' -> ('normal', 'IN')."""
    if "-" in ref and ref.split("-", 1)[0] in TAGS:
        t, cc = ref.split("-", 1)
        return t, cc
    return "normal", ref


KIND_NAME = {"account": "Account", "channel": "Channel", "group": "Group", "bot": "Bot", "method": "Method"}
STATUS_TXT = {
    "awaiting_payment": "💳 waiting for payment", "awaiting_approval": "⏳ waiting for owner approval",
    "approved": "✅ approved", "delivered": "📦 delivered – action needed", "completed": "🏁 completed",
    "rejected": "❌ rejected", "expired": "⌛ expired", "cancelled": "🚫 cancelled",
    "transferring": "🔄 transferring", "logging_out": "🔄 finishing", "manual": "🛠 owner is finishing it",
}


def bind(bot):
    global BOT
    BOT = bot


def is_owner(uid: int) -> bool:
    if uid in config.OWNER_IDS:
        return True
    try:
        import main
        if uid in getattr(main, 'EXTRA_OWNERS', []):
            return True
    except Exception:
        pass
    return False


# ---------------------------------------------------------------- formatting
def money(x) -> str:
    x = float(x or 0)
    return f"{config.CURRENCY}{int(x) if x == int(x) else round(x, 2)}"


def clean(s) -> str:
    """User-controlled text placed outside code spans: strip markdown control chars."""
    return re.sub(r"[*_`~|\[\]]", "", str(s or ""))[:80]


def who(u) -> str:
    fn = getattr(u, "first_name", "") or ""
    ln = getattr(u, "last_name", "") or ""
    parts = [str(x) for x in [fn, ln] if isinstance(x, str) and x.strip()]
    name = clean(" ".join(parts) or "User")
    un = getattr(u, "username", "")
    un_str = f" (@{un})" if isinstance(un, str) and un else ""
    uid = getattr(u, "id", 0)
    return f"{name}{un_str} [{uid}]"


def order_line(o: dict) -> str:
    return f"#{o['_id']} {KIND_ICON.get(o['kind'], '')} {clean(o.get('title'))} – {money(o['price'])}"


def friendly_err(e: Exception) -> str:
    n = e.__class__.__name__
    s = str(e) or n
    if isinstance(e, FloodWait):
        return f"Telegram flood limit – wait {e.value}s"
    if "AUTH_KEY" in s or "SESSION_REVOKED" in s or "USER_DEACTIVATED" in s or "Session dead" in s:
        return "Session is dead / logged out"
    return s[:250]


# ---------------------------------------------------------------- prices
def norm_tag(tag) -> str:
    return tag if tag in ("scam", "fake") else "normal"


def acc_key(tag: str, cc: str) -> str:
    """Price key for an account type + country. Normal keeps the old keys ('IN', '_');
    scam / fake get their own ('scam-IN', 'scam-_') so they NEVER share a price with normal."""
    tag = norm_tag(tag)
    return cc if tag == "normal" else f"{tag}-{cc}"


def price_of(shop: dict, item: dict) -> float:
    """Item price > (type + country) price > type default. 0 = not set (item can't be sold).
    Accounts: a Scam/Fake account never falls back to the Normal price of the same country."""
    if item.get("price"):
        return float(item["price"])
    p = (shop.get("prices") or {}).get(item["kind"]) or {}
    if not isinstance(p, dict):
        return float(p or 0)
    if item["kind"] == "account":
        tag = norm_tag(item.get("tag"))
        for k in (acc_key(tag, item.get("country") or ""), acc_key(tag, "_")):
            v = p.get(k)
            if v:
                return float(v)
        return 0.0
    return float(p.get(item.get("country") or "", 0) or p.get("_", 0) or 0)


def price_for_country(shop: dict, cc: str, tag: str = "normal") -> float:
    return price_of(shop, {"kind": "account", "country": cc, "tag": tag})


# ---------------------------------------------------------------- sending (flood safe)
async def retry(fn, *a, **kw):
    for _ in range(4):
        try:
            return await fn(*a, **kw)
        except FloodWait as e:
            logchan.push(f"Bot flood wait {e.value}s – retrying", "⏳")
            await asyncio.sleep(int(e.value or 3) + 1)
    return await fn(*a, **kw)


async def send(chat_id: int, text: str, rows=None):
    return await retry(ui.send, chat_id, text, rows)


async def send_photo(chat_id: int, photo: str, caption: str, rows=None):
    try:
        return await retry(lambda: safe_send(lambda ic: BOT.send_photo(
            chat_id, photo, caption=caption[:1020], reply_markup=build(rows, ic) if rows else None)))
    except Exception:
        try:
            return await retry(lambda: safe_send(lambda ic: BOT.send_document(
                chat_id, photo, caption=caption[:1020], reply_markup=build(rows, ic) if rows else None)))
        except Exception as e:
            log.warning("media send failed (%s) – sending text", e)
            return await send(chat_id, caption, rows)


async def show(q: CallbackQuery, text: str, rows, photo: str | None = None):
    """Edit the menu message (or replace it with a photo message)."""
    if photo:
        try:
            await q.message.delete()
        except Exception:
            pass
        return await send_photo(q.message.chat.id, photo, text, rows)
    return await retry(ui.edit, q, text, rows)


async def reply(m: Message, text: str, rows=None):
    return await retry(ui.reply, m, text, rows)


async def notify_owners(text: str, rows=None, photo: str | None = None):
    for oid in config.OWNER_IDS:
        try:
            if photo:
                await send_photo(oid, photo, text, rows)
            else:
                await send(oid, text, rows)
        except Exception as e:
            log.warning("owner %s not reachable: %s", oid, e)


async def notify_adder(it: dict, text: str, rows=None):
    """Tell the owner who ADDED this stock item (falls back to all owners)."""
    adder = it.get("added_by")
    if adder:
        try:
            return await send(adder, text, rows)
        except Exception as e:
            log.warning("adder %s not reachable: %s", adder, e)
    await notify_owners(text, rows)


def lg(text: str, level: str = "🛍"):
    log.info(text)
    logchan.push(text, level)


# ---------------------------------------------------------------- misc
_last_cb: dict[int, float] = {}


def too_fast(uid: int, gap: float = 0.6) -> bool:
    """Tiny per-user rate limit so button mashing can't flood the bot."""
    now = time.time()
    if now - _last_cb.get(uid, 0) < gap:
        return True
    _last_cb[uid] = now
    if len(_last_cb) > 5000:
        for k in [k for k, v in _last_cb.items() if now - v > 60]:
            _last_cb.pop(k, None)
    return False


_locks: dict[str, asyncio.Lock] = {}


def lock(key: str) -> asyncio.Lock:
    return _locks.setdefault(key, asyncio.Lock())


async def clear_state(uid: int):
    st = SSTATE.pop(uid, None)
    if st and st.get("client"):
        try:
            await st["client"].disconnect()
        except Exception:
            pass


def home_btn(owner: bool = False):
    return [B("🏠 Menu", "so:home" if owner else "u:home", P)]
