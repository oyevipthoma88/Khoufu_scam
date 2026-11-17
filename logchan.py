"""Detailed logs to a Telegram log channel – queued, batched and nicely formatted (never floods the bot)."""
import asyncio
import html
import logging
import os
import re
import time

from pyrogram.errors import FloodWait
from pyrogram.types import LinkPreviewOptions

import config

log = logging.getLogger("logchan")
NOPREV = LinkPreviewOptions(is_disabled=True)

# Log time zone offset in minutes (default India +5:30)
TZ_MIN = int(os.environ.get("LOG_TZ_OFFSET", "330"))
TZ_TXT = f"UTC{'+' if TZ_MIN >= 0 else '-'}{abs(TZ_MIN) // 60}:{abs(TZ_MIN) % 60:02d}"

# level emoji -> searchable hashtag in the channel
TAG = {
    "❌": "error", "💥": "crash", "🛑": "error", "⚠️": "warning", "⏳": "flood", "⌛": "timeout",
    "🛒": "order", "🧾": "payment", "💰": "money", "✅": "success", "🏁": "done", "📦": "sold",
    "🎟": "redeem", "🚫": "ban", "👤": "user", "🆕": "user", "▶️": "start", "⌨️": "command",
    "💬": "message", "🔘": "button", "📣": "broadcast", "👑": "owner", "➕": "account", "🗑": "removed",
    "🚀": "job", "🔤": "username", "⚙️": "settings", "🔐": "logout", "📩": "otp", "🟢": "online",
    "🤖": "stock", "📚": "stock", "📢": "stock", "🖼": "settings", "🔒": "mustjoin", "🆔": "order",
    "🛍": "shop", "•": "progress",
}
# tag -> severity  (🔴 problem · 🟡 attention · 🟢 money/success · 🔵 activity)
SEV = {
    "error": "🔴", "crash": "🔴", "ban": "🔴",
    "warning": "🟡", "flood": "🟡", "timeout": "🟡", "logout": "🟡",
    "order": "🟢", "payment": "🟢", "money": "🟢", "success": "🟢", "done": "🟢", "sold": "🟢",
    "redeem": "🟢", "online": "🟢",
}
SEV_NAME = {"🔴": "alert", "🟡": "attention", "🟢": "ok", "🔵": "info"}
_ORDER_RX = re.compile(r"(?i)order\s*#(\d+)")
_USER_RX = re.compile(r"\((\d{5,})\)|\b(?:buyer|user|by|uid)[: ]+(\d{5,})\b", re.I)


def _tags(text: str, tag: str) -> str:
    """#tag #ok + searchable #order_5 / #id12345 so the channel search finds everything."""
    sev = SEV.get(tag, "🔵")
    out = [f"#{tag}", f"#{SEV_NAME[sev]}"]
    for n in dict.fromkeys(_ORDER_RX.findall(text)):
        out.append(f"#order_{n}")
    for m in _USER_RX.finditer(text):
        uid = m.group(1) or m.group(2)
        if f"#id{uid}" not in out:
            out.append(f"#id{uid}")
    return " ".join(out[:6])


_queue: asyncio.Queue | None = None
_bot = None


def setup(bot):
    """Call once after bot.start()."""
    global _queue, _bot
    if not config.LOG_CHANNEL:
        log.info("LOG_CHANNEL not set – channel logging disabled")
        return
    _bot = bot
    _queue = asyncio.Queue(maxsize=5000)
    asyncio.create_task(_worker())


def _now(fmt: str) -> str:
    return time.strftime(fmt, time.gmtime(time.time() + TZ_MIN * 60))


def fmt_entry(text: str, level: str = "ℹ️", stamp: str | None = None) -> str:
    """One log entry as a clean card:
    🛒 <b>Order #5 created</b>
    🟢 #order #ok #order_5
    ┣ 🔹 buyer ➜ 123
    ┗ 🕒 12:30:05"""
    text = str(text).strip()[:900]
    parts = [x.strip() for x in re.split(r"\s+\|\s+", text) if x.strip()]
    head, details = (parts[0] if parts else "-"), parts[1:]
    # "Head: reason" -> head + reason as its own detail line (long error text stays readable)
    if not details and ": " in head and len(head) > 45:
        head, rest = head.split(": ", 1)
        details = [f"reason: {rest}"]
    tag = TAG.get(level) or TAG.get(level.replace("\ufe0f", "")) or "info"
    sev = SEV.get(tag, "🔵")
    out = [f"{level} <b>{html.escape(head)}</b>", f"{sev} {_tags(text, tag)}"]
    for d in details:
        if ":" in d and d.index(":") < 25:
            k, v = d.split(":", 1)
            out.append(f"┣ 🔹 <b>{html.escape(k.strip())}</b> ➜ <code>{html.escape(v.strip())}</code>")
        else:
            out.append(f"┣ ▫️ {html.escape(d)}")
    out.append(f"┗ 🕒 <code>{stamp or _now('%H:%M:%S')}</code>")
    return "<blockquote>" + "\n".join(out) + "</blockquote>"


def push(text: str, level: str = "ℹ️"):
    """Non-blocking. Safe to call from anywhere. Use ' | ' in text to split it into detail lines."""
    if _queue is None:
        return
    try:
        _queue.put_nowait(fmt_entry(text, level))
    except asyncio.QueueFull:
        pass


def _header(entries: list) -> str:
    n = len(entries)
    counts = {k: sum(1 for e in entries if f"\n{k} #" in e) for k in ("🔴", "🟡", "🟢", "🔵")}
    summary = "  ".join(f"{k} {v}" for k, v in counts.items() if v)
    return (f"📊 <b>KHOUFU • LIVE LOGS</b>\n"
            f"📅 <code>{_now('%d %b %Y')}</code>  ⏰ <code>{_now('%H:%M')}</code>  🌍 {TZ_TXT}\n"
            f"📝 <b>{n}</b> event{'s' if n != 1 else ''}   {summary}\n"
            f"#logs #d{_now('%d%b%Y').lower()}\n"
            f"━━━━━━━━━━━━━━━━━━\n")


MAX_BATCH = 3600
_carry: list[str] = []


def _fit(entry: str, limit: int = 800) -> str:
    """Ensure an entry fits well inside the batch limit; if overly long, truncate plain."""
    if len(entry) <= limit:
        return entry
    plain = re.sub(r"<[^>]+>", "", entry)
    n = limit - 50
    while n > 10 and len(html.escape(plain[:n])) > limit - 20:
        n -= 10
    return f"<blockquote>▫️ {html.escape(plain[:n])}…</blockquote>"


async def _worker():
    global _carry
    while True:
        entries = list(_carry)
        _carry = []
        if not entries:
            first = await _queue.get()
            entries.append(first)
        deadline = time.time() + 3
        while time.time() < deadline:
            try:
                item = await asyncio.wait_for(_queue.get(), max(0.05, deadline - time.time()))
                entries.append(item)
            except asyncio.TimeoutError:
                break
        hdr = _header(entries)
        to_send = []
        cur_len = len(hdr)
        for idx, e in enumerate(entries):
            e = _fit(e)
            if cur_len + len(e) + 1 > MAX_BATCH and to_send:
                _carry = entries[idx:]
                break
            to_send.append(e)
            cur_len += len(e) + 1
        text = _header(to_send) + "\n".join(to_send)
        for _ in range(3):
            try:
                await _bot.send_message(config.LOG_CHANNEL, text, parse_mode=_html(),
                                        link_preview_options=NOPREV)
                break
            except FloodWait as e:
                await asyncio.sleep(int(e.value or 5) + 1)
            except Exception as e:
                log.warning("log channel send failed: %s", e)
                break
        await asyncio.sleep(1.5)


def _html():
    from pyrogram.enums import ParseMode
    return ParseMode.HTML
