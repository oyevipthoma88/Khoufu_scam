"""Detailed logs to a Telegram log channel – queued & batched so it never floods the bot."""
import asyncio
import html
import logging
import time

from pyrogram.errors import FloodWait
from pyrogram.types import LinkPreviewOptions

import config

log = logging.getLogger("logchan")
NOPREV = LinkPreviewOptions(is_disabled=True)

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


def push(text: str, level: str = "ℹ️"):
    """Non-blocking. Safe to call from anywhere."""
    if _queue is None:
        return
    stamp = time.strftime("%H:%M:%S", time.gmtime())
    line = f"{level} <code>{stamp}</code> {html.escape(str(text))[:900]}"
    try:
        _queue.put_nowait(line)
    except asyncio.QueueFull:
        pass


async def _worker():
    while True:
        first = await _queue.get()
        lines = [first]
        # collect for up to 3 seconds -> one message instead of many
        deadline = time.time() + 3
        while time.time() < deadline and sum(len(x) + 1 for x in lines) < 3500:
            try:
                lines.append(await asyncio.wait_for(_queue.get(), max(0.05, deadline - time.time())))
            except asyncio.TimeoutError:
                break
        text = "\n".join(lines)[:4000]
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
