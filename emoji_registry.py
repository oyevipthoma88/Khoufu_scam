"""Shared Telegram-verified lookup for text, callback and URL buttons."""
import json
from pathlib import Path
import regex
ENTRIES = json.loads(Path(__file__).with_name('verified_emoji.json').read_text(encoding='utf-8'))['entries']
VERIFIED_IDS = {v['id'] for v in ENTRIES.values()}

def valid_id(value):
    s = str(value) if value is not None else ''
    return bool(s and s.isascii() and s.isdecimal() and 0 < int(s) < 2**63)

def verified_id(value):
    return str(value) if valid_id(value) and str(value) in VERIFIED_IDS else None

def emoji_id(cluster):
    entry = ENTRIES.get(cluster.replace('\ufe0f', ''))
    return verified_id(entry['id']) if entry else None

def leading_emoji(text):
    match = regex.match(r'\X', text or '')
    cluster = match.group() if match else ''
    return cluster, emoji_id(cluster)

async def verify_custom_id(client, value):
    if not valid_id(value): return None
    eid = str(value)
    if eid in VERIFIED_IDS: return eid
    stickers = await client.get_custom_emoji_stickers([eid])
    if any(str(s.custom_emoji_id) == eid for s in stickers if s.custom_emoji_id):
        VERIFIED_IDS.add(eid)
        return eid
    return None
