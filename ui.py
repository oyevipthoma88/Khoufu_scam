"""Shared button / message helpers (used by main.py and shop.py)."""
import logging

from pyrogram.enums import ButtonStyle
from pyrogram.errors import BadRequest, FloodWait, MessageNotModified
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup as M, Message

import database as db
from logchan import NOPREV

log = logging.getLogger("ui")
_bot = None


def bind(bot):
    global _bot
    _bot = bot


def U(text: str, url: str) -> InlineKeyboardButton:
    """URL button."""
    return InlineKeyboardButton(text, url=url)

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

# Real premium (custom) emoji IDs from Telegram's official "RestrictedEmoji" pack (checked via Bot API getStickerSet).
# Used automatically for every button whose label starts with one of these emojis.
# Owner Panel -> Button Emojis still overrides any of them.
PREMIUM = {
    "➕": "5226945370684140473", "👤": "5373012449597335010", "📖": "5226512880362332956",
    "🚀": "5445284980978621387", "🤖": "5372981976804366741", "🔗": "5375129357373165375",
    "📊": "5431577498364158238", "✅": "5427009714745517609", "❌": "5465665476971471368",
    "👑": "5467406098367521267", "✏": "5334673106202010226", "🛍": "5373052667671093676",
    "🛒": "5431499171045581032", "📱": "5407025283456835913", "👥": "5372926953978341366",
    "📚": "5373098009640836781", "📣": "5469903029144657419", "📢": "5469903029144657419",
    "🎟": "5377599075237502153", "🎭": "5359441070201513074", "💰": "5375296873982604963",
    "🖼": "5375074927252621134", "🏦": "5264895611517300926", "🏠": "5465226866321268133",
    "✨": "5472164874886846699", "🎉": "5436040291507247633", "🔐": "5472308992514464048",
    "🔒": "5472308992514464048", "💸": "5472030678633684592", "🌍": "5399898266265475100",
    "🔄": "5264727218734524899", "♻": "5264727218734524899", "🧾": "5334882760735598374",
    "📝": "5334882760735598374", "⚠": "5467928559664242360", "❗": "5467928559664242360",
    "📦": "5199749070830197566", "🎁": "5199749070830197566", "🔑": "5330115548900501467",
    "💳": "5375296873982604963", "📩": "5406631276042002796", "📞": "5467539229468793355",
    "⚡": "5431449001532594346", "👋": "5472055112702629499", "💚": "5449380056201697322",
    "🔥": "5420315771991497307", "💎": "5471952986970267163", "⭐": "5435957248314579621",
}
# Same-meaning aliases (reuse the verified IDs above) so more buttons get a premium icon.
for _a, _src in {"🗑": "❌", "🚫": "❌", "🏁": "🎉", "🔁": "🔄", "🆔": "🔑", "🔤": "✏", "🎨": "✨",
                 "💬": "📝", "👛": "💸", "🟢": "✅", "⏳": "⚡", "📤": "🚀", "🔓": "🔐", "📋": "🧾",
                 "🌐": "🌍", "📲": "📱", "🏷": "🎟", "🙋": "👋"}.items():
    PREMIUM.setdefault(_a, PREMIUM[_src])


def _auto_icon(text: str):
    head = text.split(" ", 1)[0].replace("\ufe0f", "") if text else ""
    return PREMIUM.get(head)


def B(text: str, data: str, style: ButtonStyle = D, key: str | None = None) -> tuple:
    return (text, data, style, key)


def build(rows, icons: bool | None = None) -> M:
    icons = UI["icons"] if icons is None else icons
    out = []
    for row in rows:
        r = []
        for b in row:
            if isinstance(b, InlineKeyboardButton):
                r.append(b)
                continue
            text, data, style, key = b
            eid = (UI["emojis"].get(key) or _auto_icon(text)) if icons else None
            if eid is not None and not str(eid).isdigit():
                eid = None  # broken/old value in DB (e.g. 'add') – ignore instead of crashing
            if eid:
                # premium icon replaces the plain emoji at the start of the label
                text = text.split(" ", 1)[1] if " " in text and not text[0].isalnum() else text
            if style == D:  # no plain grey buttons – every button gets a colour
                style = R if data and any(x in data for x in (":cx:", ":del", ":rj:", "clear", "picdel", "owrm")) else P
            r.append(InlineKeyboardButton(text, icon_custom_emoji_id=str(eid) if eid else None,
                                          style=style, callback_data=data))
        out.append(r)
    return M(out)


BACK = [B("🔙 Back", "home", D, "back")]


def back(to: str) -> list:
    return [B("🔙 Back", to, D, "back")]


async def safe_send(coro_factory):
    """Send with premium icons; if Telegram rejects them, retry once without and remember."""
    try:
        return await coro_factory(None)
    except MessageNotModified:
        return None
    except (BadRequest, ValueError) as e:
        if not UI["icons"]:  # icons already off -> real error
            raise
        log.warning("Button icons rejected (%s) – disabling icons", e)
        UI["icons"] = False
        await db.set_ui("icons", False)
        return await coro_factory(False)


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

