import pytest
import shop_core as core
import database as db
import logchan

def test_parse_amount_valid():
    assert core.parse_amount("100") == 100.0
    assert core.parse_amount("1,250.50") == 1250.50
    assert core.parse_amount("0", allow_zero=True) == 0.0

def test_parse_amount_invalid():
    with pytest.raises(ValueError):
        core.parse_amount("0", allow_zero=False)
    with pytest.raises(ValueError):
        core.parse_amount("-10")
    with pytest.raises(ValueError):
        core.parse_amount("nan")
    with pytest.raises(ValueError):
        core.parse_amount("inf")
    with pytest.raises(ValueError):
        core.parse_amount("invalid")

def test_cap():
    lines = [f"line {i}" for i in range(30)]
    capped = core.cap(lines, 10)
    assert len(capped) == 11
    assert "and 20 more" in capped[-1]
    short = core.cap(lines[:5], 10)
    assert len(short) == 5

def test_logchan_fit():
    short = "<blockquote>Hello</blockquote>"
    assert logchan._fit(short) == short
    long_entry = "<blockquote>" + "A" * 1200 + "</blockquote>"
    fitted = logchan._fit(long_entry, limit=200)
    assert len(fitted) <= 200
    assert fitted.startswith("<blockquote>▫️ ")
    assert fitted.endswith("…</blockquote>")

@pytest.mark.asyncio
async def test_set_state_disconnects_old_client():
    class DummyClient:
        def __init__(self):
            self.disconnected = False
        async def disconnect(self):
            self.disconnected = True

    dummy = DummyClient()
    core.SSTATE[999] = {"step": "test", "client": dummy}
    await core.set_state(999, {"step": "new"})
    assert dummy.disconnected is True
    assert core.SSTATE[999]["step"] == "new"
    await core.clear_state(999)
