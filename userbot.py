"""Everything that runs on the logged-in (user) accounts."""
import asyncio
import base64
import logging
import os
import random
import string
import struct
import tempfile
from contextlib import asynccontextmanager

from pyrogram import Client, raw, types
from pyrogram.errors import (
    ChannelsAdminPublicTooMuch,
    Flood,
    FloodWait,
    RPCError,
    UsernameInvalid,
    UsernameNotModified,
    UsernameOccupied,
    UsernamePurchaseAvailable,
)

import config
import database as db
import logchan

log = logging.getLogger(__name__)

from pyrogram.errors import (AuthKeyUnregistered, AuthKeyDuplicated, UserDeactivated,
                             UserDeactivatedBan, SessionRevoked)

# One lock per account: the same account is never worked on by two jobs at once,
# but different accounts run in parallel (limited by config.PARALLEL_ACCOUNTS).
ACCOUNT_LOCKS: dict[int, asyncio.Lock] = {}
ACTIVE: set[int] = set()
_COUNTER_LOCKS: dict[str, asyncio.Lock] = {}


def account_lock(acc_id: int) -> asyncio.Lock:
    return ACCOUNT_LOCKS.setdefault(acc_id, asyncio.Lock())


def busy() -> bool:
    return bool(ACTIVE)

USERNAME_TAKEN = (UsernameOccupied, UsernameInvalid, UsernamePurchaseAvailable)


# ---------------------------------------------------------------- helpers
def new_client(session: str | None = None, name: str = "acc") -> Client:
    return Client(
        name=f"{name}_{random.randint(1, 10**9)}",
        api_id=config.API_ID,
        api_hash=config.API_HASH,
        session_string=session,
        in_memory=True,
        no_updates=True,
    )


@asynccontextmanager
async def account_client(session: str):
    c = new_client(session)
    try:
        await c.start()
    except (AuthKeyUnregistered, AuthKeyDuplicated, UserDeactivated, UserDeactivatedBan, SessionRevoked) as e:
        raise RuntimeError(f"Session dead ({e.__class__.__name__}) – remove this account and log in again") from e
    try:
        yield c
    finally:
        try:
            await c.stop()
        except Exception:
            pass


class FloodTooLong(Exception):
    pass


async def safe(fn, *args, **kwargs):
    """Run a Telegram call. On any flood error (FloodWait / Slowmode / PremiumWait) sleep the
    exact time Telegram asks (+ jitter) and retry. Very long waits are not blocked on."""
    name = getattr(fn, "__name__", "call")
    for attempt in range(4):
        try:
            return await fn(*args, **kwargs)
        except Flood as e:
            wait = int(getattr(e, "value", 0) or 10)
            if wait > config.MAX_FLOOD_WAIT:
                logchan.push(f"Flood {wait}s on {name} – too long, step skipped", "🛑")
                raise FloodTooLong(f"Telegram flood limit {wait}s on {name} – skipped") from e
            wait += 2 + random.randint(0, 3)
            log.warning("Flood %ss on %s (try %s)", wait, name, attempt + 1)
            logchan.push(f"Flood wait {wait}s on {name} (try {attempt + 1})", "⏳")
            await asyncio.sleep(wait)
    return await fn(*args, **kwargs)


async def pause():
    # small random jitter makes the pattern less bot-like -> fewer flood limits
    await asyncio.sleep(config.ACTION_DELAY + random.uniform(0, config.ACTION_DELAY / 2))


def write_tmp(data: bytes) -> str:
    fd, path = tempfile.mkstemp(suffix=".jpg")
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    return path


def fmt_name(template: str, n: int) -> str:
    """Owner can put {n} inside a name/bio to get 1,2,3... per channel/group."""
    return template.replace("{n}", str(n)) if template else template


def random_word(k: int = 8) -> str:
    return random.choice(string.ascii_lowercase) + "".join(
        random.choices(string.ascii_lowercase + string.digits, k=k)
    )


def link_of(chat: dict) -> str:
    if chat.get("username"):
        return f"https://t.me/{chat['username']}"
    return chat.get("invite") or f"(private id {chat['id']})"


def telethon_to_pyrogram(s: str) -> tuple[str, bool]:
    """Convert a Telethon string session to Pyrogram format if needed.
    Returns (session, needs_user_id_fix)."""
    s = s.strip().strip("'").strip('"')
    raw_body = s[1:].rstrip("=")
    if s.startswith("1") and len(raw_body) in (351, 367):
        data = base64.urlsafe_b64decode(raw_body + "=" * (-len(raw_body) % 4))
        ip_len = 4 if len(data) == 263 else 16
        dc_id, _ip, _port, key = struct.unpack(f">B{ip_len}sH256s", data)
        packed = struct.pack(">BI?256sQ?", dc_id, config.API_ID, False, key, 1, False)
        return base64.urlsafe_b64encode(packed).decode().rstrip("="), True
    return s, False


# ---------------------------------------------------------------- snapshot
async def snapshot_profile(c: Client) -> dict:
    me = await c.get_me()
    bio = None
    try:
        bio = (await c.get_chat("me")).bio
    except Exception:
        pass
    pfp = None
    try:
        async for p in c.get_chat_photos("me", limit=1):
            fid = getattr(p, "file_id", None) or getattr(p, "big_file_id", None)
            mem = await c.download_media(fid, in_memory=True)
            pfp = bytes(mem.getbuffer())
            break
    except Exception as e:
        log.warning("Could not save original photo: %s", e)
    return {
        "first_name": me.first_name or "",
        "last_name": me.last_name or "",
        "username": me.username,
        "bio": bio or "",
        "pfp": pfp,
    }


async def login_with_string(session: str) -> tuple[int, str, dict, dict]:
    """Validate a string session. Returns (user_id, fresh_session, info, original)."""
    session, fix = telethon_to_pyrogram(session)
    c = new_client(session, "str")
    await c.start()
    try:
        me = await c.get_me()
        if fix:
            await c.storage.user_id(me.id)
        fresh = await c.export_session_string()
        original = await snapshot_profile(c)
        return me.id, fresh, user_info(me), original
    finally:
        try:
            await c.stop()
        except Exception:
            pass


def user_info(me) -> dict:
    return {
        "name": " ".join(x for x in [me.first_name, me.last_name] if x) or "No name",
        "username": me.username,
        "phone": me.phone_number,
    }


# ---------------------------------------------------------------- photos
async def delete_all_profile_photos(c: Client):
    try:
        res = await c.invoke(raw.functions.photos.GetUserPhotos(
            user_id=raw.types.InputUserSelf(), offset=0, max_id=0, limit=100))
        ids = [raw.types.InputPhoto(id=p.id, access_hash=p.access_hash, file_reference=p.file_reference)
               for p in res.photos if isinstance(p, raw.types.Photo)]
        if ids:
            await safe(c.invoke, raw.functions.photos.DeletePhotos(id=ids))
    except Exception as e:
        log.warning("delete photos failed: %s", e)


async def set_user_photo(c: Client, data: bytes):
    path = write_tmp(data)
    try:
        await safe(c.set_profile_photo, photo=types.InputChatPhotoStatic(photo=path))
    finally:
        os.remove(path)


async def set_chat_photo(c: Client, chat_id: int, data: bytes):
    path = write_tmp(data)
    try:
        await safe(c.set_chat_photo, chat_id, photo=path)
    finally:
        os.remove(path)


# ---------------------------------------------------------------- usernames
async def claim_username(kind: str, base: str, setter) -> str | None:
    """Try base, base1, base2 ... (shared counter across all accounts, stored in Mongo)."""
    base = base.strip().lstrip("@")
    key = f"{kind}:{base.lower()}"
    async with _COUNTER_LOCKS.setdefault(key, asyncio.Lock()):
        return await _claim(key, base, setter)


async def _claim(key: str, base: str, setter) -> str | None:
    n = await db.get_counter(key)
    for _ in range(config.USERNAME_TRIES):
        cand = base if n == 0 else f"{base}{n}"
        n += 1
        if len(cand) < 5:
            continue
        try:
            await safe(setter, cand)
        except UsernameNotModified:
            pass
        except USERNAME_TAKEN:
            continue
        await db.set_counter(key, n)
        logchan.push(f"Username claimed @{cand} ({key})", "🔤")
        return cand
    await db.set_counter(key, n)
    return None


# ---------------------------------------------------------------- apply
async def apply_user(c: Client, s: dict, report) -> None:
    if not any(s.get(f) for f in db.FIELDS):
        await report("👤 Profile: no settings, skipped")
        return
    name = s.get("name")
    bio = s.get("bio")
    if name or bio is not None:
        first, _, last = (name or "").partition(" ")
        kwargs = {}
        if name:
            kwargs.update(first_name=first, last_name=last)
        if bio:
            kwargs["bio"] = bio[:70]
        if kwargs:
            await safe(c.update_profile, **kwargs)
            await pause()
    if s.get("pfp"):
        await set_user_photo(c, s["pfp"])
        await pause()
    if s.get("username"):
        me = await c.get_me()
        base = s["username"].lstrip("@").lower()
        if me.username and me.username.lower().startswith(base):
            await report(f"👤 Username kept: @{me.username}")
        else:
            got = await claim_username("user", s["username"], c.set_username)
            await report(f"👤 Username: @{got}" if got else "👤 Username: no free name found")
    await report("👤 Profile updated ✅")


async def _configure_chat(c: Client, kind: str, chat: dict, s: dict, idx: int, report, public_ok: list):
    cid = chat["id"]
    if s.get("name"):
        try:
            await safe(c.set_chat_title, cid, fmt_name(s["name"], idx)[:128])
        except RPCError as e:
            if "NOT_MODIFIED" not in str(e):
                raise
        chat["title"] = fmt_name(s["name"], idx)
        await pause()
    if s.get("bio"):
        try:
            await safe(c.set_chat_description, cid, fmt_name(s["bio"], idx)[:255])
        except RPCError as e:
            if "NOT_MODIFIED" not in str(e):
                raise
        await pause()
    if s.get("pfp"):
        await set_chat_photo(c, cid, s["pfp"])
        await pause()
    if s.get("username") and public_ok[0]:
        base = s["username"].lstrip("@").lower()
        if chat.get("username") and chat["username"].lower().startswith(base):
            return
        try:
            got = await claim_username(kind, s["username"], lambda u: c.set_chat_username(cid, u))
            if got:
                chat["username"] = got
        except ChannelsAdminPublicTooMuch:
            public_ok[0] = False
            await report("⚠️ Telegram limit: this account can't own more public links. Rest stay private.")
        await pause()
    if not chat.get("username"):
        try:
            chat["invite"] = await safe(c.export_chat_invite_link, cid)
        except Exception:
            pass


async def apply_chats(c: Client, acc: dict, kind: str, s: dict, report) -> list[dict]:
    want = config.CHANNEL_COUNT if kind == "channel" else config.GROUP_COUNT
    label = "📢 Channel" if kind == "channel" else "👥 Group"
    chats: list[dict] = list(acc.get("chats", {}).get(kind, []))
    if not any(s.get(f) for f in db.FIELDS):
        await report(f"{label}s: no settings, skipped")
        return chats

    # drop chats that were deleted
    alive = []
    for ch in chats:
        try:
            await c.get_chat(ch["id"])
            alive.append(ch)
        except Exception:
            pass
    chats = alive
    public_ok = [True]

    for i, ch in enumerate(chats, start=1):
        await _configure_chat(c, kind, ch, s, i, report, public_ok)
        await db.update_account(acc["_id"], {f"chats.{kind}": chats})

    while len(chats) < want:
        idx = len(chats) + 1
        title = fmt_name(s.get("name") or f"{kind.title()} {idx}", idx)[:128]
        desc = fmt_name(s.get("bio") or "", idx)[:255]
        try:
            if kind == "channel":
                chat = await safe(c.create_channel, title, desc)
            else:
                chat = await safe(c.create_supergroup, title, desc)
        except RPCError as e:
            await report(f"{label} create failed: {e.ID if hasattr(e, 'ID') else e}")
            break
        entry = {"id": chat.id, "title": title, "username": None}
        chats.append(entry)
        await db.update_account(acc["_id"], {f"chats.{kind}": chats})
        await pause()
        await _configure_chat(c, kind, entry, {**s, "name": None, "bio": None}, idx, report, public_ok)
        await db.update_account(acc["_id"], {f"chats.{kind}": chats})
        await report(f"{label} {idx}/{want}: {link_of(entry)}")
        await pause()
    return chats


async def apply_account(acc_id: int, report) -> list[str]:
    acc = await db.get_account(acc_id)
    if not acc:
        await report("Account not found")
        return []
    s = await db.get_settings()
    name = acc.get("info", {}).get("name", acc_id)
    await report(f"▶️ Applying on {name} ({acc_id})")
    errors: list[str] = []
    async with account_client(acc["session"]) as c:
        async def track(label, coro):
            try:
                await coro
            except Exception as e:
                log.exception("%s failed on %s", label, acc_id)
                errors.append(f"{label}: {e}")
                await report(f"❌ {label} error: {e}")

        # profile, channels and groups run in parallel on the same account
        await asyncio.gather(
            track("Profile", apply_user(c, s["user"], report)),
            track("Channels", apply_chats(c, acc, "channel", s["channel"], report)),
            track("Groups", apply_chats(c, acc, "group", s["group"], report)),
        )
        me = await c.get_me()
        await db.update_account(acc_id, {"info": user_info(me), "applied": not errors})
    if errors:
        logchan.push(f"Apply on {name} ({acc_id}) had errors: " + " | ".join(errors), "⚠️")
    return account_links(await db.get_account(acc_id))


def account_links(acc: dict) -> list[str]:
    out = []
    for kind, label in (("channel", "📢"), ("group", "👥")):
        for ch in acc.get("chats", {}).get(kind, []):
            out.append(f"{label} {ch.get('title', '')} — {link_of(ch)}")
    return out


# ---------------------------------------------------------------- reset
async def reset_account(acc_id: int, report) -> list[str]:
    """Restore the original profile; randomize all channels/groups."""
    acc = await db.get_account(acc_id)
    if not acc:
        return []
    orig = acc.get("original", {}) or {}
    await report(f"♻️ Resetting {acc.get('info', {}).get('name', acc_id)}")
    async with account_client(acc["session"]) as c:
        try:
            await safe(c.update_profile, first_name=orig.get("first_name") or "User",
                       last_name=orig.get("last_name") or "", bio=orig.get("bio") or "")
            await pause()
            try:
                await safe(c.set_username, orig.get("username"))
            except (UsernameNotModified, *USERNAME_TAKEN):
                pass
            await delete_all_profile_photos(c)
            if orig.get("pfp"):
                await set_user_photo(c, orig["pfp"])
            await report("👤 Old profile restored ✅")
        except RPCError as e:
            await report(f"👤 Restore error: {e}")

        chats = acc.get("chats", {})
        for kind in ("channel", "group"):
            for ch in chats.get(kind, []):
                cid = ch["id"]
                try:
                    title = random_word(6).title() + " " + random_word(4).title()
                    await safe(c.set_chat_title, cid, title)
                    ch["title"] = title
                    await pause()
                    try:
                        await safe(c.set_chat_description, cid, random_word(12))
                    except RPCError:
                        pass
                    try:
                        await safe(c.delete_chat_photo, cid)
                    except RPCError:
                        pass
                    if ch.get("username"):
                        for _ in range(10):
                            u = random_word(10)
                            try:
                                await safe(c.set_chat_username, cid, u)
                                ch["username"] = u
                                break
                            except USERNAME_TAKEN:
                                continue
                    await pause()
                except RPCError as e:
                    await report(f"chat {cid} error: {e}")
        await db.update_account(acc_id, {"chats": chats, "applied": False})
        me = await c.get_me()
        await db.update_account(acc_id, {"info": user_info(me)})
    return account_links(await db.get_account(acc_id))
