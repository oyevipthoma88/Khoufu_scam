"""Telegram-side work for the shop (runs on the stored seller / stock accounts)."""
import asyncio
import json
import logging
import re
import time
import urllib.request

import phonenumbers
from phonenumbers import geocoder
from pyrogram import raw
from pyrogram.enums import ChatMemberStatus, ChatMembersFilter, ChatType
from pyrogram.errors import PeerIdInvalid, UserNotParticipant
from pyrogram.types import ChatPrivileges

import userbot

log = logging.getLogger("shop_tg")

OTP_RE = re.compile(r"(?i)code\D{0,12}(\d{5,6})")
GONE = (ChatMemberStatus.LEFT, ChatMemberStatus.BANNED)


# ---------------------------------------------------------------- phone / country
def flag(code: str) -> str:
    if not code or len(code) != 2 or not code.isalpha():
        return "🏳️"
    return "".join(chr(0x1F1E6 + ord(c) - 65) for c in code.upper())


def country_of(phone: str | None) -> tuple[str, str]:
    """('IN', 'India') from a phone number; ('XX', 'Unknown') if it can't be detected."""
    if not phone:
        return "XX", "Unknown"
    try:
        num = phonenumbers.parse("+" + phone.lstrip("+"), None)
        code = phonenumbers.region_code_for_number(num) or "XX"
        name = geocoder.country_name_for_number(num, "en") or code
        return code, name
    except Exception:
        return "XX", "Unknown"


def extract_code(text: str | None) -> str | None:
    if not text:
        return None
    m = OTP_RE.search(text.replace("-", ""))
    return m.group(1) if m else None


def spaced(code: str) -> str:
    return " ".join(code)


# ---------------------------------------------------------------- sessions
async def validate_session(text: str) -> dict:
    """Start a string session, read who it is. Returns a dict ready for the stock."""
    session, fix = userbot.telethon_to_pyrogram(text.strip())
    c = userbot.new_client(session, "stk")
    await c.start()
    try:
        me = await c.get_me()
        if fix:
            await c.storage.user_id(me.id)
        fresh = await c.export_session_string()
    finally:
        try:
            await c.stop()
        except Exception:
            pass
    return account_doc(me, fresh)


def account_doc(me, session: str) -> dict:
    phone = (me.phone_number or "").lstrip("+")
    cc, cname = country_of(phone)
    return {
        "uid": me.id, "session": session, "phone": phone, "country": cc, "country_name": cname,
        "name": " ".join(x for x in [me.first_name, me.last_name] if x) or "No name",
        "username": me.username,
    }


async def wait_for_otp(session: str, seen_id: int = 0, timeout: int = 120, max_age: int = 300):
    """Poll the 'Telegram' service chat (777000) of a stock account for a fresh login code.
    Returns (code, message_id) or (None, seen_id)."""
    async with userbot.account_client(session) as c:
        end = time.time() + timeout
        while True:
            try:
                async for msg in c.get_chat_history(777000, limit=6):
                    if msg.id <= seen_id:
                        continue
                    if msg.date and time.time() - msg.date.timestamp() > max_age:
                        continue
                    code = extract_code(msg.text or msg.caption)
                    if code:
                        return code, msg.id
            except Exception as e:  # a hiccup must not kill the wait
                log.warning("otp poll error: %s", e)
            if time.time() >= end:
                return None, seen_id
            await asyncio.sleep(4)


async def other_sessions(c) -> int:
    r = await c.invoke(raw.functions.account.GetAuthorizations())
    return sum(1 for a in r.authorizations if not a.current)


async def logout_stock(session: str, force: bool = False) -> tuple[bool, str]:
    """Log OUR session out. Unless force, first make sure the buyer's own session exists.
    Returns (done, reason)."""
    try:
        async with userbot.account_client(session) as c:
            if not force and await other_sessions(c) == 0:
                return False, "no_other_session"
            await c.log_out()
            return True, "ok"
    except RuntimeError as e:  # session already dead = we are already logged out
        if "Session dead" in str(e):
            return True, "already"
        raise


# ---------------------------------------------------------------- chats (channels / groups)
def parse_chat_ref(text: str):
    """t.me link / @name / -100 id -> something get_chat() understands."""
    t = text.strip()
    if not t:
        return None
    if re.fullmatch(r"-?\d{5,}", t):
        return int(t)
    m = re.search(r"(?:https?://)?(?:t|telegram)\.me/c/(\d+)", t)
    if m:
        return int("-100" + m.group(1))
    m = re.search(r"(?:https?://)?(?:t|telegram)\.me/(\+[\w-]+|joinchat/[\w-]+)", t)
    if m:
        return "https://t.me/" + m.group(1)
    m = re.search(r"(?:https?://)?(?:t|telegram)\.me/([A-Za-z][\w]{3,})", t)
    if m:
        return m.group(1)
    if re.fullmatch(r"@?[A-Za-z][\w]{3,}", t):
        return t.lstrip("@")
    return None


async def inspect_chat(c, ref, want: str) -> dict:
    """Checks that the seller account OWNS the chat. want = 'channel' | 'group'."""
    chat = await c.get_chat(ref)
    if not getattr(chat, "id", None) or getattr(chat, "type", None) is None:
        raise ValueError("Account is not a member of that chat")
    if want == "channel" and chat.type != ChatType.CHANNEL:
        raise ValueError("That is not a channel (it's a group) – use Add GC")
    if want == "group":
        if chat.type == ChatType.GROUP:
            raise ValueError("Basic group – convert it to a supergroup first (ownership can't move otherwise)")
        if chat.type != ChatType.SUPERGROUP:
            raise ValueError("That is not a group – use Add Channel")
    me = await c.get_chat_member(chat.id, "me")
    if me.status != ChatMemberStatus.OWNER:
        raise ValueError("This account is not the OWNER of that chat")
    return {
        "chat_id": chat.id, "title": chat.title or "Untitled", "username": chat.username,
        "members": chat.members_count or 0, "desc": (chat.description or "")[:200],
    }


async def make_join_link(session: str, item: dict, label: str) -> str:
    """Public chats -> t.me/username. Private chats -> a fresh single-use invite link."""
    if item.get("username"):
        return f"https://t.me/{item['username']}"
    async with userbot.account_client(session) as c:
        inv = await c.create_chat_invite_link(item["chat_id"], name=label[:30], member_limit=1)
        return inv.invite_link


async def find_member(c, chat_id: int, user_id: int, hint: str = ""):
    """ChatMember or None. Fresh sessions don't know the user yet, so scan recent members."""
    try:
        m = await c.get_chat_member(chat_id, user_id)
        return None if m.status in GONE else m
    except UserNotParticipant:
        return None
    except (PeerIdInvalid, KeyError, ValueError):
        pass
    async for m in c.get_chat_members(chat_id, filter=ChatMembersFilter.RECENT, limit=200):
        if m.user and m.user.id == user_id:
            return None if m.status in GONE else m
    if hint:
        async for m in c.get_chat_members(chat_id, query=hint[:30], filter=ChatMembersFilter.SEARCH, limit=50):
            if m.user and m.user.id == user_id:
                return None if m.status in GONE else m
    return None


class NotJoined(Exception):
    pass


async def check_and_transfer(session: str, password: str | None, chat_id: int, user_id: int,
                             hint: str = "") -> str:
    """Verify the buyer joined, make them admin and hand over ownership. Returns 'ok'."""
    async with userbot.account_client(session) as c:
        m = await find_member(c, chat_id, user_id, hint)
        if not m:
            raise NotJoined()
        if not password:
            raise PermissionError("PASSWORD_MISSING")
        try:
            await c.promote_chat_member(chat_id, user_id, ChatPrivileges(
                can_manage_chat=True, can_change_info=True, can_post_messages=True, can_edit_messages=True,
                can_delete_messages=True, can_invite_users=True, can_restrict_members=True,
                can_pin_messages=True, can_promote_members=True, can_manage_video_chats=True))
        except Exception as e:
            log.info("promote before transfer: %s", e)  # maybe already admin – transfer decides
        await c.transfer_chat_ownership(chat_id, user_id, password)
        try:
            await c.leave_chat(chat_id)
        except Exception:
            pass
        return "ok"


def transfer_error(e: Exception) -> tuple[str, bool]:
    """(human text, retry_later). retry_later=False means the owner has to step in."""
    n = e.__class__.__name__
    s = str(e)
    up = (n + " " + s).upper()
    digits = re.findall(r"\d{3,}", s)
    hours = f"{max(1, round(int(digits[0]) / 3600))}h" if digits else "some hours"
    if "PASSWORD_MISSING" in up or "PASSWORDMISSING" in up:
        return "Seller account has no 2-step password set, so Telegram blocks the transfer.", False
    if "PASSWORDHASHINVALID" in up or "PASSWORD_HASH_INVALID" in up:
        return "Stored 2-step password of the seller account is wrong.", False
    if "SESSIONTOOFRESH" in up or "SESSION_TOO_FRESH" in up:
        return f"Telegram only allows transfer ~24h after the seller session was created. Try again in about {hours}.", True
    if "PASSWORDTOOFRESH" in up or "PASSWORD_TOO_FRESH" in up:
        return f"Seller 2-step password was changed recently. Try again in about {hours}.", True
    if "CHANNELSADMINPUBLICTOOMUCH" in up or "PUBLIC_TOO_MUCH" in up:
        return "The new owner already owns too many public chats.", True
    if "USERPRIVACY" in up or "USER_PRIVACY" in up or "USERNOTMUTUAL" in up:
        return "Privacy settings of the receiving account block the transfer.", True
    if "CHATADMINREQUIRED" in up or "CHAT_ADMIN_REQUIRED" in up or "CHATWRITEFORBIDDEN" in up:
        return "Seller account is no longer the owner of this chat.", False
    return f"{n}: {s[:150]}", False


# ---------------------------------------------------------------- bot tokens
def _getme(token: str) -> dict:
    with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/getMe", timeout=15) as r:
        return json.loads(r.read().decode())


async def check_bot_token(token: str) -> dict:
    if not re.fullmatch(r"\d{6,}:[\w-]{30,}", token):
        raise ValueError("That doesn't look like a bot token")
    try:
        data = await asyncio.to_thread(_getme, token)
    except Exception:
        raise ValueError("Telegram rejected this token (revoked or wrong)")
    if not data.get("ok"):
        raise ValueError("Telegram rejected this token")
    return data["result"]
