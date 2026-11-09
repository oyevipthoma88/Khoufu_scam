import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
for k, v in {"API_ID": "1", "API_HASH": "x", "BOT_TOKEN": "1:x", "MONGO_URI": "mongodb://localhost", "OWNER_IDS": "1"}.items():
    os.environ.setdefault(k, v)
from types import SimpleNamespace as NS
import ui


def _msg(icon):
    return NS(reply_markup=NS(inline_keyboard=[[NS(icon_custom_emoji_id=icon)]]))


def test_dropped_icons_detected_as_blocked():
    ui.ICON_STATUS.update(state="unknown", reason="")
    asyncio.run(ui.safe_send(lambda ic: asyncio.sleep(0, _msg(None))))
    assert ui.ICON_STATUS["state"] == "dropped"
    assert "BLOCKED" in ui.icon_status_line()


def test_echoed_icons_detected_as_live():
    ui.ICON_STATUS.update(state="unknown", reason="")
    asyncio.run(ui.safe_send(lambda ic: asyncio.sleep(0, _msg("5368324170671202286"))))
    assert ui.ICON_STATUS["state"] == "active"
