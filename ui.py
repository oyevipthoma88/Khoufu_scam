"""Shared button / message helpers (used by main.py and shop.py)."""
import contextvars
import logging
import os
import time
from copy import copy
from emoji_registry import leading_emoji, verified_id

from pyrogram.enums import ButtonStyle, ChatType
from pyrogram.errors import FloodWait, MessageNotModified, RPCError
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup as M, Message

import database as db
from logchan import NOPREV

log = logging.getLogger("ui")
_bot = None


def bind(bot, force: bool = False):
    """First client wins (see shop_core.bind); main() re-binds the started client with force=True."""
    global _bot
    if _bot is not None and _bot is not bot and not force:
        log.warning("Ignored attempt to re-bind ui to a different bot client")
        return
    _bot = bot


def U(text: str, url: str) -> InlineKeyboardButton:
    """URL button. Kept raw: build() adds the icon, so a no-icon fallback still has its normal emoji."""
    return InlineKeyboardButton(text, url=url, style=ButtonStyle.PRIMARY)

# ------------------------------------------------------------ buttons (colour + premium emoji)
# Each button spec: (text, callback_data, style, emoji_key)
G, R, P, D = ButtonStyle.SUCCESS, ButtonStyle.DANGER, ButtonStyle.PRIMARY, ButtonStyle.DEFAULT
EMOJI_KEYS = {
    "add": "➕ Add Account", "accs": "👤 My Accounts", "custom": "⚙️ Custom", "tut": "📖 Tutorial",
    "apply": "🚀 Apply", "auto": "🤖 Auto", "links": "🔗 Links", "status": "📊 Status",
    "back": "🔙 Back", "yes": "✅ Yes", "no": "❌ No", "reset": "♻️ Reset", "remove": "🗑 Remove",
    "owner": "👑 Owner Panel", "edit": "✏️ Edit fields",
    "shopadm": "🛍 Shop Admin", "shopusr": "🛒 User View",
    "u_acc": "📱 Buy Account", "u_chan": "📢 Buy Channel", "u_grp": "👥 Buy Group", "u_bot": "🤖 Buy Bot",
    "u_meth": "📚 Buy Method", "u_owner": "👑 Owner", "u_upd": "📣 Update Channel", "u_tut": "📖 Tutorial",
    "u_redeem": "🎟 Redeem Code", "u_orders": "🧾 My Orders",
    "t_normal": "✅ Normal Account", "t_scam": "⚠️ Scam Tag Account", "t_fake": "🎭 Fake Tag Account",
    "addstock": "➕ Add Stock", "stock": "📦 Stock List", "orders": "🧾 Orders", "price": "💰 Set Price",
    "qr": "🖼 Set QR", "upi": "🏦 Set UPI", "codes": "🎟 Redeem Codes", "force": "🔒 Must-Join",
    "menu": "🏠 Menu", "buynow": "💳 Buy now", "paid": "✅ I have paid", "otp": "📩 Get OTP",
    "approve": "✅ Approve", "reject": "❌ Reject", "addowner": "👑 Owners",
}
UI = {"emojis": {}, "icons": True, "start_pic": None}

# Premium icon for every button whose label starts with an emoji (flags too). 1000+ verified IDs.
# Owner Panel -> Button Emojis still overrides any of them.
from premium_emoji import PREMIUM  # noqa: E402


def _auto_icon(text: str):
    return leading_emoji(text)[1]


def _decorate(button, icons, override=None):
    button = copy(button)
    cluster, automatic = leading_emoji(button.text)
    eid = (verified_id(override) or verified_id(button.icon_custom_emoji_id) or automatic
           or PREMIUM["▫"]) if icons else None
    if eid and automatic:
        label = button.text[len(cluster):].lstrip(" ")
        if label:
            button.text = label
    button.icon_custom_emoji_id = eid
    return button


def B(text: str, data: str, style: ButtonStyle = D, key: str | None = None) -> tuple:
    return (text, data, style, key)


def _build(rows, icons: bool) -> M:
    out = []
    for row in rows:
        r = []
        for b in row:
            if isinstance(b, InlineKeyboardButton):
                r.append(_decorate(b, icons))
                continue
            text, data, style, key = b
            if style == D:  # no plain grey buttons – every button gets a colour
                style = R if data and any(x in data for x in (":cx:", ":del", ":rj:", "clear", "picdel", "owrm")) else P
            r.append(_decorate(InlineKeyboardButton(text, style=style, callback_data=data),
                               icons, UI["emojis"].get(key)))
        out.append(r)
    return M(out)


def build(rows, icons: bool | None = None) -> M:
    """icons=None -> the icon engine decides (see icons_wanted)."""
    icons = icons_wanted() if icons is None else bool(icons)
    markup = _build(rows, icons)
    n = _sent_icons(markup)
    _LAST.set((n, _build(rows, False) if n else None))
    return markup


BACK = [B("🔙 Back", "home", D, "back")]


def back(to: str) -> list:
    return [B("🔙 Back", to, D, "back")]


# ------------------------------------------------------------ premium icon engine
# Telegram only shows button icons when the account that CREATED the bot in @BotFather has Telegram
# Premium (or the bot owns a Fragment username), and never in channels. Without that Telegram either
# rejects the request or silently strips the icons. The engine below:
#   * judges only private/group chats (a channel refusal says nothing about the bot owner),
#   * blames the icons only when the same message goes through without them,
#   * repairs a message whose icons were stripped so its buttons keep their normal emoji,
#   * then sends plain-emoji buttons (no wasted double requests) and re-tests every ICON_PROBE_EVERY s,
#     so icons come back by themselves once Premium is active.
PROBE_EVERY = max(60, int(os.environ.get("ICON_PROBE_EVERY", "1800")))
ICON_STATUS = {"state": "unknown", "reason": "", "since": 0.0, "retry_at": 0.0, "checked": 0.0}
_ICON_ERRORS = ("CUSTOM_EMOJI", "DOCUMENT_INVALID", "BUTTON_STYLE", "BUTTON_ICON", "PREMIUM",
                "EMOJI_INVALID", "ICON_INVALID", "STYLE_INVALID")
_LAST: contextvars.ContextVar = contextvars.ContextVar("icon_last", default=(0, None))  # (icons sent, plain markup)
LISTENERS: list = []  # fn(old_state, new_state) – main.py alerts the owners


def icons_wanted() -> bool:
    """Should the next keyboard carry premium icons?"""
    if not UI["icons"]:
        return False
    if ICON_STATUS["state"] in ("dropped", "rejected"):
        return time.time() >= ICON_STATUS["retry_at"]  # time for a fresh test
    return True


def set_icon_state(state: str, reason: str = ""):
    old, now = ICON_STATUS["state"], time.time()
    ICON_STATUS.update(state=state, reason=reason, checked=now)
    if old != state:
        ICON_STATUS["since"] = now
    ICON_STATUS["retry_at"] = now + PROBE_EVERY if state in ("dropped", "rejected") else 0.0
    if old != state:
        (log.info if state == "active" else log.warning)("Premium button icons: %s -> %s %s", old, state, reason)
        for fn in list(LISTENERS):
            try:
                fn(old, state)
            except Exception as e:  # an alert must never break a send
                log.warning("icon listener failed: %s", e)


def force_icon_test():
    """Owner pressed 'Test now': the very next keyboard carries icons again."""
    ICON_STATUS["retry_at"] = 0.0


def _sent_icons(markup) -> int:
    try:
        return sum(1 for row in markup.inline_keyboard for b in row if getattr(b, "icon_custom_emoji_id", None))
    except Exception:
        return 0


def _judgeable(result) -> bool:
    """Only private / group chats tell us something about the bot owner's Premium."""
    chat = getattr(result, "chat", None)
    return getattr(chat, "type", None) != ChatType.CHANNEL


def _icon_error(e: Exception) -> bool:
    err = f"{getattr(e, 'ID', '') or ''} {e}".upper()
    return any(x in err for x in _ICON_ERRORS)


async def _judge(result, fallback):
    """Telegram echoes the keyboard back. Icons missing there = Telegram silently stripped them."""
    markup = getattr(result, "reply_markup", None)
    if markup is None or not getattr(markup, "inline_keyboard", None) or not _judgeable(result):
        return
    if _sent_icons(markup):
        return set_icon_state("active")
    set_icon_state("dropped", "Telegram removed the premium icons. The account that created this bot in "
                              "@BotFather needs an active Telegram Premium subscription.")
    if fallback is not None and hasattr(result, "edit_reply_markup"):
        try:  # this message lost its emojis together with the icons – give it normal ones back
            await result.edit_reply_markup(fallback)
        except Exception as e:
            log.debug("icon repair skipped: %s", e)


async def safe_send(coro_factory):
    """coro_factory(ic) must build its keyboard with build(rows, ic). ic=None -> engine decides, False -> plain."""
    _LAST.set((0, None))
    try:
        result = await coro_factory(None)
    except MessageNotModified:
        return None
    except (RPCError, ValueError) as e:
        sent = _LAST.get()[0]
        if isinstance(e, FloodWait) or not sent or not _icon_error(e):
            raise
        try:
            result = await coro_factory(False)
        except MessageNotModified:
            result = None
        except Exception:
            raise e  # still failing without icons -> the icons were not the problem
        if result is None or _judgeable(result):
            set_icon_state("rejected", f"Telegram refused the icons ({getattr(e, 'ID', None) or e}). The account "
                                       "that created this bot in @BotFather needs Telegram Premium.")
        return result
    sent, fallback = _LAST.get()
    if sent:
        await _judge(result, fallback)
    return result


def _ago(ts: float) -> str:
    if not ts:
        return "never"
    s = int(time.time() - ts)
    return f"{s}s ago" if s < 60 else f"{s // 60}m ago" if s < 3600 else f"{s // 3600}h ago"


def icon_status_line() -> str:
    st = ICON_STATUS["state"]
    if not UI["icons"]:
        return "🔴 Premium icons: **OFF** (switched off in Owner Panel)"
    if st == "active":
        return "🟢 Premium icons: **LIVE** on buttons"
    if st in ("dropped", "rejected"):
        left = max(0, int(ICON_STATUS["retry_at"] - time.time()))
        return (f"🔴 Premium icons: **{'BLOCKED' if st == 'dropped' else 'REJECTED'}** – {ICON_STATUS['reason']}\n"
                f"👉 Buttons use normal emojis meanwhile. Next auto-test in {left // 60}m {left % 60}s.")
    return "🟡 Premium icons: checking… (open any menu)"


async def icon_report(client, owner_ids) -> str:
    """Full diagnosis for the Owner Panel."""
    lines = ["🧪 **Premium Icon Check**", "━━━━━━━━━━━━━━━━━━", icon_status_line(),
             f"🕒 Last result: {_ago(ICON_STATUS['checked'])}"]
    try:
        users = await client.get_users(list(owner_ids)[:20]) if owner_ids else []
        users = users if isinstance(users, list) else [users]
        if users:
            lines.append("\n👑 **Owners' Telegram Premium**")
            lines += [f"{'✅' if getattr(u, 'is_premium', False) else '❌'} {u.first_name or ''} `{u.id}`" for u in users]
    except Exception as e:
        log.debug("owner premium lookup failed: %s", e)
    if ICON_STATUS["state"] != "active":
        lines += ["\n🛠 **How to fix**",
                  "1️⃣ Open @BotFather from the account that **created** this bot",
                  "2️⃣ That account must have **Telegram Premium** (an owner ID alone is not enough)",
                  "3️⃣ Press **🧪 Test now** – icons switch on by themselves, no restart needed"]
    return "\n".join(lines)


async def reply(m: Message, text: str, rows=None):
    return await safe_send(lambda ic: m.reply(text, reply_markup=build(rows, ic) if rows else None,
                                              link_preview_options=NOPREV))


async def send(chat_id: int, text: str, rows=None):
    return await safe_send(lambda ic: _bot.send_message(chat_id, text, reply_markup=build(rows, ic) if rows else None,
                                                       link_preview_options=NOPREV))


async def edit(q: CallbackQuery, text: str, rows):
    msg = q.message
    if msg.photo:  # can't turn a photo message into text – send a fresh one
        if len(text) <= 1000:
            try:
                return await safe_send(lambda ic: msg.edit_caption(text, reply_markup=build(rows, ic) if rows else None))
            except Exception:
                pass
        try:
            await msg.delete()
        except Exception:
            pass
        return await send(msg.chat.id, text, rows)
    try:
        return await safe_send(lambda ic: msg.edit_text(text, reply_markup=build(rows, ic) if rows else None,
                                                        link_preview_options=NOPREV))
    except Exception:
        return await send(msg.chat.id, text, rows)

