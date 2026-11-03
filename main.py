"""Khoufu Account Manager Bot – owner-only control panel."""
import asyncio
import logging
import time

from pyrogram import Client, filters, idle
from pyrogram.errors import (
    PasswordHashInvalid,
    PhoneCodeExpired,
    PhoneCodeInvalid,
    PhoneNumberInvalid,
    SessionPasswordNeeded,
)
from pyrogram.types import CallbackQuery, InlineKeyboardButton as B, InlineKeyboardMarkup as M, Message

import config
import database as db
import userbot

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("pyrogram").setLevel(logging.WARNING)
log = logging.getLogger("bot")

config.check()

bot = Client("khoufu_bot", api_id=config.API_ID, api_hash=config.API_HASH,
             bot_token=config.BOT_TOKEN, in_memory=True)

OWNER = filters.user(config.OWNER_IDS)
STATE: dict[int, dict] = {}  # per-owner conversation state
KIND_LABEL = {"user": "👤 User", "channel": "📢 Channel", "group": "👥 Group"}
FIELD_LABEL = {"name": "Name", "bio": "Bio", "pfp": "PFP", "username": "Username"}

TUTORIAL = """📖 **Tutorial**

**1. Setup (one time)**
• ⚙️ **Custom** → 👤 Set User / 📢 Set Channel / 👥 Set Group
• Each has: Set Name, Set Bio, Set PFP, Set Username
• Tip: write `{n}` in a channel/group name or bio to get 1, 2, 3…

**2. Add accounts**
• ➕ **Add Account** → 🔑 String Session (Pyrogram or Telethon) or 📱 Phone Login
• For phone login, send the OTP **with spaces** like `1 2 3 4 5` (otherwise Telegram blocks it)

**3. Apply**
• 🚀 **Apply All** → applies your settings to every account
• 🤖 **Auto Apply ON** → every new account is changed right after login
• Each account gets its profile changed + 10 public channels + 10 public groups

**4. Usernames**
Same username can't be used twice, so the bot tries `name`, `name1`, `name2`… and sets the first free one. Same for channels and groups.

**5. My Accounts**
• See all accounts, tap ♻️ to reset: old name, bio, photo, username come back and channels/groups get random names. You get all links at once.

⚠️ Telegram allows only a limited number of public links per account. Extra ones stay private with an invite link."""


def main_kb(auto: bool) -> M:
    return M([
        [B("➕ Add Account", "add"), B("👤 My Accounts", "accs")],
        [B("⚙️ Custom", "custom"), B("📖 Tutorial", "tut")],
        [B("🚀 Apply All", "apply_all"), B(f"🤖 Auto: {'ON' if auto else 'OFF'}", "auto")],
        [B("🔗 All Links", "links_all"), B("📊 Status", "status")],
    ])


BACK = [B("🔙 Back", "home")]


# ------------------------------------------------------------ progress reporter
class Reporter:
    def __init__(self, msg: Message):
        self.msg, self.lines, self.last = msg, [], 0.0

    async def __call__(self, line: str):
        log.info(line)
        self.lines.append(line)
        if time.time() - self.last > 3:
            await self.flush()

    async def flush(self):
        self.last = time.time()
        try:
            await self.msg.edit_text("⏳ **Working…**\n\n" + "\n".join(self.lines[-20:]),
                                     disable_web_page_preview=True)
        except Exception:
            pass


async def send_long(chat_id: int, header: str, lines: list[str]):
    chunk = header
    for line in lines or ["(no links yet)"]:
        if len(chunk) + len(line) + 1 > 3900:
            await bot.send_message(chat_id, chunk, disable_web_page_preview=True)
            chunk = ""
        chunk += "\n" + line
    await bot.send_message(chat_id, chunk, disable_web_page_preview=True)


async def run_job(chat_id: int, title: str, ids: list[int], fn):
    """Background job over many accounts (queued, one at a time)."""
    msg = await bot.send_message(chat_id, f"⏳ {title} queued…")
    rep = Reporter(msg)
    all_links: list[str] = []
    async with userbot.JOB_LOCK:
        for acc_id in ids:
            try:
                links = await fn(acc_id, rep)
                acc = await db.get_account(acc_id)
                head = f"\n**{acc.get('info', {}).get('name', acc_id)}** ({acc_id})"
                all_links += [head] + links
            except Exception as e:
                log.exception("job failed")
                await rep(f"❌ {acc_id}: {e}")
    await rep(f"✅ {title} finished")
    await rep.flush()
    await send_long(chat_id, f"🔗 **{title} – links**", all_links)


# ------------------------------------------------------------ commands
@bot.on_message(filters.private & ~OWNER)
async def not_owner(_, m: Message):
    await m.reply("🔒 This is a private bot.")


@bot.on_callback_query(~OWNER)
async def not_owner_cb(_, q: CallbackQuery):
    await q.answer("🔒 Private bot", show_alert=True)


@bot.on_message(filters.private & OWNER & filters.command(["start", "menu"]))
async def start(_, m: Message):
    STATE.pop(m.from_user.id, None)
    s = await db.get_settings()
    n = len(await db.all_accounts())
    await m.reply(f"👋 **Welcome, owner!**\n\nAccounts added: **{n}**\nChoose an option:",
                  reply_markup=main_kb(s["auto"]))


@bot.on_message(filters.private & OWNER & filters.command("cancel"))
async def cancel(_, m: Message):
    st = STATE.pop(m.from_user.id, None)
    if st and st.get("client"):
        try:
            await st["client"].disconnect()
        except Exception:
            pass
    await m.reply("❌ Cancelled.", reply_markup=M([BACK]))


# ------------------------------------------------------------ callbacks
async def edit(q: CallbackQuery, text: str, kb):
    try:
        await q.message.edit_text(text, reply_markup=M(kb) if isinstance(kb, list) else kb,
                                  disable_web_page_preview=True)
    except Exception:
        await q.message.reply(text, reply_markup=M(kb) if isinstance(kb, list) else kb)


@bot.on_callback_query(OWNER)
async def callbacks(_, q: CallbackQuery):
    d, uid = q.data, q.from_user.id
    await q.answer()

    if d == "home":
        STATE.pop(uid, None)
        s = await db.get_settings()
        n = len(await db.all_accounts())
        return await edit(q, f"🏠 **Main Menu**\n\nAccounts added: **{n}**", main_kb(s["auto"]))

    if d == "tut":
        return await edit(q, TUTORIAL, [BACK])

    if d == "auto":
        on = await db.toggle_auto()
        return await edit(q, f"🤖 Auto apply is now **{'ON' if on else 'OFF'}**", main_kb(on))

    if d == "status":
        s = await db.get_settings()
        accs = await db.all_accounts()
        busy = "🟡 busy" if userbot.JOB_LOCK.locked() else "🟢 idle"
        lines = [f"📊 **Status** – {busy}", f"Accounts: {len(accs)}", f"Auto apply: {s['auto']}", ""]
        for k in db.KINDS:
            lines.append(f"**{KIND_LABEL[k]}**: " + ", ".join(
                f"{FIELD_LABEL[f]} {'✅' if s[k].get(f) else '❌'}" for f in db.FIELDS))
        return await edit(q, "\n".join(lines), [BACK])

    # ---- custom panel
    if d == "custom":
        return await edit(q, "⚙️ **Custom Panel**\nWhat do you want to set?", [
            [B("👤 Set User", "k:user")],
            [B("📢 Set Channel", "k:channel")],
            [B("👥 Set Group", "k:group")],
            BACK,
        ])

    if d.startswith("k:"):
        kind = d[2:]
        s = (await db.get_settings())[kind]
        view = "\n".join(
            f"• {FIELD_LABEL[f]}: " + ("🖼 set" if f == "pfp" and s.get(f) else f"`{s.get(f)}`" if s.get(f) else "—")
            for f in db.FIELDS)
        return await edit(q, f"{KIND_LABEL[kind]} **settings**\n\n{view}", [
            [B("✏️ Set Name", f"f:{kind}:name"), B("📝 Set Bio", f"f:{kind}:bio")],
            [B("🖼 Set PFP", f"f:{kind}:pfp"), B("🔤 Set Username", f"f:{kind}:username")],
            [B("🗑 Clear all", f"clr:{kind}")],
            [B("🔙 Back", "custom")],
        ])

    if d.startswith("f:"):
        _, kind, field = d.split(":")
        STATE[uid] = {"step": "field", "kind": kind, "field": field}
        hint = {
            "name": "Send the new **name**." + (" Use `{n}` for numbers." if kind != "user" else ""),
            "bio": "Send the new **bio** (user max 70 chars, channel/group max 255).",
            "pfp": "Send the **photo** to use.",
            "username": "Send the base **username** (without @). Bot will try name, name1, name2…",
        }[field]
        return await edit(q, f"{KIND_LABEL[kind]} → {hint}\n\n/cancel to stop", [[B("🔙 Back", f"k:{kind}")]])

    if d.startswith("clr:"):
        kind = d[4:]
        await db.clear_kind(kind)
        return await edit(q, f"🗑 {KIND_LABEL[kind]} settings cleared.", [[B("🔙 Back", f"k:{kind}")]])

    # ---- add account
    if d == "add":
        return await edit(q, "➕ **Add Account**\nHow do you want to log in?", [
            [B("🔑 String Session", "add:str")],
            [B("📱 Phone Login", "add:phone")],
            BACK,
        ])
    if d == "add:str":
        STATE[uid] = {"step": "string"}
        return await edit(q, "🔑 Send the **string session** (Pyrogram or Telethon).\n\n/cancel to stop", [BACK])
    if d == "add:phone":
        STATE[uid] = {"step": "phone"}
        return await edit(q, "📱 Send the **phone number** with country code, e.g. `+919876543210`\n\n/cancel to stop",
                          [BACK])

    # ---- accounts
    if d == "accs":
        accs = await db.all_accounts()
        if not accs:
            return await edit(q, "No accounts yet.", [[B("➕ Add Account", "add")], BACK])
        kb = []
        for a in accs:
            info = a.get("info", {})
            label = info.get("name", str(a["_id"]))[:20]
            mark = "✅" if a.get("applied") else "⏸"
            kb.append([B(f"{mark} {label}", f"acc:{a['_id']}"), B("♻️ Reset", f"rst?:{a['_id']}")])
        kb += [[B("♻️ Reset All", "rst?:all"), B("🔗 All Links", "links_all")], BACK]
        return await edit(q, f"👤 **My Accounts** ({len(accs)})\n✅ applied  ⏸ not applied", kb)

    if d.startswith("acc:"):
        a = await db.get_account(int(d[4:]))
        if not a:
            return await edit(q, "Account not found.", [BACK])
        info = a.get("info", {})
        ch = a.get("chats", {})
        text = (f"👤 **{info.get('name')}**\nID: `{a['_id']}`\nUsername: @{info.get('username') or '—'}\n"
                f"Phone: {info.get('phone') or '—'}\n"
                f"Channels: {len(ch.get('channel', []))} | Groups: {len(ch.get('group', []))}")
        return await edit(q, text, [
            [B("🚀 Apply", f"ap:{a['_id']}"), B("♻️ Reset", f"rst?:{a['_id']}")],
            [B("🔗 Links", f"lk:{a['_id']}"), B("❌ Remove", f"rm?:{a['_id']}")],
            [B("🔙 Back", "accs")],
        ])

    if d.startswith("lk:"):
        a = await db.get_account(int(d[3:]))
        return await send_long(uid, f"🔗 **Links – {a.get('info', {}).get('name')}**", userbot.account_links(a))

    if d == "links_all":
        lines = []
        for a in await db.all_accounts():
            lines.append(f"\n**{a.get('info', {}).get('name')}** ({a['_id']})")
            lines += userbot.account_links(a)
        return await send_long(uid, "🔗 **All Links**", lines)

    if d.startswith("ap:"):
        acc_id = int(d[3:])
        asyncio.create_task(run_job(uid, "Apply", [acc_id], userbot.apply_account))
        return await edit(q, "🚀 Apply started. Progress below 👇", [BACK])

    if d == "apply_all":
        ids = [a["_id"] for a in await db.all_accounts()]
        if not ids:
            return await edit(q, "No accounts yet.", [BACK])
        asyncio.create_task(run_job(uid, "Apply All", ids, userbot.apply_account))
        return await edit(q, f"🚀 Applying on {len(ids)} accounts. Progress below 👇", [BACK])

    if d.startswith("rst?:"):
        target = d[5:]
        return await edit(q, "♻️ **Reset?**\nOld name, bio, photo & username come back.\n"
                             "Channels/groups get random names & usernames.", [
            [B("✅ Yes, reset", f"rst:{target}"), B("❌ No", "accs")]])

    if d.startswith("rst:"):
        target = d[4:]
        ids = [a["_id"] for a in await db.all_accounts()] if target == "all" else [int(target)]
        asyncio.create_task(run_job(uid, "Reset", ids, userbot.reset_account))
        return await edit(q, "♻️ Reset started. Progress below 👇", [BACK])

    if d.startswith("rm?:"):
        return await edit(q, "❌ Remove this account from the bot?\n(Telegram account itself is not deleted)", [
            [B("✅ Yes", f"rm:{d[4:]}"), B("❌ No", f"acc:{d[4:]}")]])
    if d.startswith("rm:"):
        await db.remove_account(int(d[3:]))
        return await edit(q, "🗑 Account removed.", [[B("🔙 Back", "accs")]])


# ------------------------------------------------------------ text / photo input
async def finish_login(m: Message, user_id: int, session: str, info: dict, original: dict):
    await db.save_account(user_id, session, info, original)
    s = await db.get_settings()
    await m.reply(f"✅ **Account added:** {info['name']} (`{user_id}`)",
                  reply_markup=M([[B("🚀 Apply now", f"ap:{user_id}")], BACK]))
    if s["auto"]:
        asyncio.create_task(run_job(m.chat.id, "Auto Apply", [user_id], userbot.apply_account))


@bot.on_message(filters.private & OWNER & (filters.text | filters.photo) & ~filters.command(["start", "menu", "cancel"]))
async def inputs(_, m: Message):
    uid = m.from_user.id
    st = STATE.get(uid)
    if not st:
        return await m.reply("Use /start to open the menu.")
    step = st["step"]

    # ---- settings
    if step == "field":
        kind, field = st["kind"], st["field"]
        if field == "pfp":
            if not m.photo:
                return await m.reply("Please send a photo.")
            mem = await bot.download_media(m.photo.file_id, in_memory=True)
            await db.set_field(kind, "pfp", bytes(mem.getbuffer()))
        else:
            if not m.text:
                return await m.reply("Please send text.")
            val = m.text.strip()
            if field == "username":
                val = val.lstrip("@")
                if not val.replace("_", "").isalnum() or not val[0].isalpha():
                    return await m.reply("Invalid username. Use letters, numbers, _ and start with a letter.")
            await db.set_field(kind, field, val)
        STATE.pop(uid, None)
        return await m.reply(f"✅ {KIND_LABEL[kind]} {FIELD_LABEL[field]} saved.\n"
                             "It will be used on all accounts (tap Apply All, or keep Auto ON).",
                             reply_markup=M([[B("🔙 Back", f"k:{kind}"), B("🚀 Apply All", "apply_all")]]))

    if not m.text:
        return
    text = m.text.strip()

    # ---- string session
    if step == "string":
        STATE.pop(uid, None)
        try:
            await m.delete()
        except Exception:
            pass
        wait = await m.reply("⏳ Checking session…")
        try:
            user_id, session, info, original = await userbot.login_with_string(text)
        except Exception as e:
            return await wait.edit_text(f"❌ Invalid session: {e}", reply_markup=M([BACK]))
        await wait.delete()
        return await finish_login(m, user_id, session, info, original)

    # ---- phone login
    if step == "phone":
        phone = text.replace(" ", "")
        c = userbot.new_client(None, "login")
        await c.connect()
        try:
            sent = await c.send_code(phone)
        except PhoneNumberInvalid:
            await c.disconnect()
            return await m.reply("❌ Invalid phone number. Try again or /cancel.")
        except Exception as e:
            await c.disconnect()
            STATE.pop(uid, None)
            return await m.reply(f"❌ {e}", reply_markup=M([BACK]))
        STATE[uid] = {"step": "code", "client": c, "phone": phone, "hash": sent.phone_code_hash}
        return await m.reply("📩 OTP sent. Send it **with spaces**, e.g. `1 2 3 4 5`\n/cancel to stop")

    if step == "code":
        c = st["client"]
        code = "".join(ch for ch in text if ch.isdigit())
        try:
            await m.delete()
        except Exception:
            pass
        try:
            await c.sign_in(st["phone"], st["hash"], code)
        except SessionPasswordNeeded:
            st["step"] = "password"
            return await m.reply("🔐 2-step verification is on. Send the **password**.\n/cancel to stop")
        except (PhoneCodeInvalid, PhoneCodeExpired) as e:
            return await m.reply(f"❌ {e.__class__.__name__}. Send the code again or /cancel.")
        except Exception as e:
            STATE.pop(uid, None)
            await c.disconnect()
            return await m.reply(f"❌ Login failed: {e}", reply_markup=M([BACK]))
        return await _phone_done(m, uid, c)

    if step == "password":
        c = st["client"]
        try:
            await m.delete()
        except Exception:
            pass
        try:
            await c.check_password(text)
        except PasswordHashInvalid:
            return await m.reply("❌ Wrong password. Try again or /cancel.")
        except Exception as e:
            STATE.pop(uid, None)
            await c.disconnect()
            return await m.reply(f"❌ Login failed: {e}", reply_markup=M([BACK]))
        return await _phone_done(m, uid, c)


async def _phone_done(m: Message, uid: int, c: Client):
    STATE.pop(uid, None)
    try:
        session = await c.export_session_string()
        me = await c.get_me()
        original = await userbot.snapshot_profile(c)
    finally:
        await c.disconnect()
    await finish_login(m, me.id, session, userbot.user_info(me), original)


# ------------------------------------------------------------ run
async def main():
    await bot.start()
    me = await bot.get_me()
    log.info("Bot started as @%s – owners: %s", me.username, config.OWNER_IDS)
    for oid in config.OWNER_IDS:
        try:
            await bot.send_message(oid, "✅ Bot is online. Send /start")
        except Exception:
            pass
    await idle()
    await bot.stop()


if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(main())
