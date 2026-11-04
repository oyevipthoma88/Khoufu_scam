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
from pyrogram.enums import ButtonStyle, MessageEntityType
from pyrogram.errors import BadRequest, FloodWait, MessageNotModified, QueryIdInvalid
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup as M, Message

import config
import database as db
import logchan
import shop
import ui
import userbot
from logchan import NOPREV
import premium_text
premium_text.install()  # premium emojis in every bot message text

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("pyrogram").setLevel(logging.WARNING)
log = logging.getLogger("bot")

config.check()

bot = Client("khoufu_bot", api_id=config.API_ID, api_hash=config.API_HASH,
             bot_token=config.BOT_TOKEN, in_memory=True)
ui.bind(bot)
shop.register(bot)

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

**6. Owner Panel**
• 🖼 Set a start picture (saved forever in database)
• ✨ Button Emojis: tap a button, send any premium emoji → it becomes that button's icon
• 🎨 Icons ON/OFF switch

⚠️ Telegram allows only a limited number of public links per account. Extra ones stay private with an invite link."""


from ui import (B, BACK, D, EMOJI_KEYS, G, P, R, UI, back, build, edit, reply, safe_send, send)


def main_kb(auto: bool) -> list:
    return [
        [B("➕ Add Account", "add", G, "add"), B("👤 My Accounts", "accs", P, "accs")],
        [B("⚙️ Custom", "custom", P, "custom"), B("📖 Tutorial", "tut", D, "tut")],
        [B("🚀 Apply All", "apply_all", G, "apply"), B(f"🤖 Auto: {'ON' if auto else 'OFF'}", "auto", G if auto else R, "auto")],
        [B("🔗 All Links", "links_all", P, "links"), B("📊 Status", "status", D, "status")],
        [B("🛍 Shop Admin", "so:home", G, "shopadm"), B("🛒 User View", "u:home", P, "shopusr")],
        [B("👑 Owner Panel", "owner", P, "owner")],
    ]


EXTRA_OWNERS: list[int] = []  # owners added from the bot (saved in DB)


def set_owner(oid: int, on: bool):
    """Add/remove an owner at runtime (filters + config + list)."""
    for coll in (config.OWNER_IDS, EXTRA_OWNERS):
        if on and oid not in coll:
            coll.append(oid)
        if not on and oid in coll:
            coll.remove(oid)
    for f in (OWNER, shop.OWNER):
        (f.add if on else f.discard)(oid)


async def home_text() -> str:
    n = len(await db.all_accounts())
    busy = "🟡 working" if userbot.busy() else "🟢 idle"
    return (f"👑 **OWNER DASHBOARD**\n━━━━━━━━━━━━━━━━━━\n👤 Accounts added: **{n}**\n⚙️ Bot: {busy}\n"
            f"👥 Owners: **{len(config.OWNER_IDS)}**\n━━━━━━━━━━━━━━━━━━\n💡 /help shows all commands")


async def send_home(chat_id: int):
    s = await db.get_settings()
    text, rows = await home_text(), main_kb(s["auto"])
    if UI["start_pic"]:
        try:
            return await safe_send(lambda ic: bot.send_photo(chat_id, UI["start_pic"], caption=text,
                                                             reply_markup=build(rows, ic)))
        except Exception as e:
            log.warning("start pic failed: %s", e)
    return await send(chat_id, text, rows)


# ------------------------------------------------------------ progress reporter
class Reporter:
    def __init__(self, msg: Message | None, chat_id: int):
        self.msg, self.chat_id, self.lines, self.last = msg, chat_id, [], 0.0

    async def __call__(self, line: str):
        log.info(line)
        logchan.push(line, "•")
        self.lines.append(str(line)[:300])
        if time.time() - self.last > 6:  # max 1 edit / 6s -> no bot flood
            await self.flush()

    async def flush(self, done: bool = False):
        self.last = time.time()
        head = "✅ **Done**" if done else "⏳ **Working…**"
        text = head + "\n\n" + "\n".join(self.lines[-20:])
        try:
            if self.msg:
                await self.msg.edit_text(text[:4000], link_preview_options=NOPREV)
        except MessageNotModified:
            pass
        except FloodWait as e:
            self.last = time.time() + e.value  # just skip edits until Telegram allows again
        except Exception as e:
            log.warning("progress edit failed: %s", e)
            if done:
                try:
                    self.msg = await bot.send_message(self.chat_id, text[:4000], link_preview_options=NOPREV)
                except Exception:
                    pass


async def bot_send(chat_id: int, text: str):
    for _ in range(3):
        try:
            return await bot.send_message(chat_id, text, link_preview_options=NOPREV)
        except FloodWait as e:
            await asyncio.sleep(int(e.value or 3) + 1)


async def send_long(chat_id: int, header: str, lines: list[str]):
    chunk = header
    for line in lines or ["(no links yet)"]:
        if len(chunk) + len(line) + 1 > 3900:
            await bot_send(chat_id, chunk)
            chunk = ""
            await asyncio.sleep(1)
        chunk += "\n" + line
    await bot_send(chat_id, chunk)


async def run_job(chat_id: int, title: str, ids: list[int], fn):
    """Background job over many accounts. Accounts run in parallel (PARALLEL_ACCOUNTS),
    the same account is never processed twice at once. Never crashes the bot."""
    try:
        clash = [i for i in ids if userbot.account_lock(i).locked()]
        note = f"\n⚠️ {len(clash)} account(s) busy in another job – they will wait." if clash else ""
        msg = await bot.send_message(chat_id, f"⏳ {title} started on {len(ids)} account(s) "
                                              f"({config.PARALLEL_ACCOUNTS} at a time)…{note}")
        rep = Reporter(msg, chat_id)
        logchan.push(f"JOB START: {title} – {len(ids)} account(s)", "🚀")
        results: dict[int, list[str]] = {}
        errors: list[str] = []
        sem = asyncio.Semaphore(config.PARALLEL_ACCOUNTS)
        t0 = time.time()

        async def one(i: int, acc_id: int):
            async with sem, userbot.account_lock(acc_id):
                userbot.ACTIVE.add(acc_id)
                await rep(f"— [{i}/{len(ids)}] account {acc_id}")
                try:
                    links = await fn(acc_id, rep)
                    acc = await db.get_account(acc_id) or {}
                    head = f"\n**{acc.get('info', {}).get('name', acc_id)}** ({acc_id})"
                    results[acc_id] = [head] + (links or [])
                    logchan.push(f"{title} OK on {acc_id} – {len(links or [])} link(s)", "✅")
                except Exception as e:
                    log.exception("job failed for %s", acc_id)
                    errors.append(f"{acc_id}: {friendly(e)}")
                    await rep(f"❌ {acc_id}: {friendly(e)}")
                    logchan.push(f"{title} FAILED on {acc_id}: {friendly(e)}", "❌")
                finally:
                    userbot.ACTIVE.discard(acc_id)

        await asyncio.gather(*(one(i, a) for i, a in enumerate(ids, 1)))
        ok, fail = len(results), len(errors)
        took = int(time.time() - t0)
        await rep(f"🏁 {title} finished in {took}s – ✅ {ok}  ❌ {fail}")
        await rep.flush(done=True)
        logchan.push(f"JOB END: {title} – ok {ok}, failed {fail}, {took}s", "🏁")
        all_links = [x for acc_id in ids for x in results.get(acc_id, [])]
        await send_long(chat_id, f"🔗 **{title} – links**", all_links)
        if errors:
            await send_long(chat_id, f"⚠️ **{title} – errors**", errors)
        await send(chat_id, "What next?", [[B("🏠 Main Menu", "home", P, "back")]])
    except Exception as e:
        log.exception("run_job crashed")
        logchan.push(f"{title} crashed: {friendly(e)}", "💥")
        try:
            await bot.send_message(chat_id, f"⚠️ {title} stopped: {friendly(e)}")
        except Exception:
            pass


def friendly(e: Exception) -> str:
    name = e.__class__.__name__
    msg = str(e) or name
    if isinstance(e, FloodWait):
        return f"Telegram flood limit – wait {e.value}s and try again"
    if isinstance(e, userbot.FloodTooLong):
        return msg
    if "AUTH_KEY" in msg or "SESSION_REVOKED" in msg or "USER_DEACTIVATED" in msg:
        return "Session expired / account logged out – remove it and add again"
    return msg[:300]


def parse_id(text: str) -> int | None:
    try:
        return int(text)
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------ commands
async def drop_state(uid: int):
    await shop.clear_state(uid)
    st = STATE.pop(uid, None)
    if st and st.get("client"):
        try:
            await st["client"].disconnect()
        except Exception:
            pass


@bot.on_message(filters.private & OWNER & filters.command(["start", "menu"]))
async def start(_, m: Message):
    await drop_state(m.from_user.id)
    await send_home(m.chat.id)


@bot.on_message(filters.private & OWNER & filters.command("cancel"))
async def cancel(_, m: Message):
    await drop_state(m.from_user.id)
    await reply(m, "❌ Cancelled.", [BACK])


# ------------------------------------------------------------ callbacks
@bot.on_callback_query(OWNER & ~filters.regex(r"^(u|so):"))
async def callbacks(_, q: CallbackQuery):
    try:
        await q.answer()
    except QueryIdInvalid:
        pass
    except Exception:
        pass
    try:
        await shop.clear_state(q.from_user.id)  # leaving the shop flow
        await handle_cb(q)
    except Exception as e:
        log.exception("callback %s failed", q.data)
        logchan.push(f"Button '{q.data}' error: {friendly(e)}", "❌")
        try:
            await send(q.message.chat.id, f"⚠️ **Error:** {friendly(e)}", [BACK])
        except Exception:
            pass


async def handle_cb(q: CallbackQuery):
    d, uid = q.data or "", q.from_user.id

    if d == "home":
        await drop_state(uid)
        s = await db.get_settings()
        if UI["start_pic"]:
            try:
                await q.message.delete()
            except Exception:
                pass
            return await send_home(q.message.chat.id)
        return await edit(q, await home_text(), main_kb(s["auto"]))

    if d == "tut":
        return await edit(q, TUTORIAL, [BACK])

    if d == "auto":
        on = await db.toggle_auto()
        return await edit(q, f"🤖 Auto apply is now **{'ON' if on else 'OFF'}**\n\n" + await home_text(), main_kb(on))

    if d == "status":
        s = await db.get_settings()
        accs = await db.all_accounts()
        busy = "🟡 busy" if userbot.busy() else "🟢 idle"
        lines = [f"📊 **Status** – {busy}", f"Accounts: {len(accs)}",
                 f"Applied: {sum(1 for a in accs if a.get('applied'))}",
                 f"Auto apply: {'ON' if s['auto'] else 'OFF'}",
                 f"Start pic: {'✅' if UI['start_pic'] else '❌'} | Premium icons: {len(UI['emojis'])} "
                 f"({'on' if UI['icons'] else 'off'})", ""]
        for k in db.KINDS:
            lines.append(f"**{KIND_LABEL[k]}**: " + ", ".join(
                f"{FIELD_LABEL[f]} {'✅' if s[k].get(f) else '❌'}" for f in db.FIELDS))
        return await edit(q, "\n".join(lines), [[B("🔄 Refresh", "status", P)], BACK])

    # ---- owner panel
    if d == "owner":
        return await edit(q, "👑 **Owner Panel**\n\n"
                             f"🖼 Start pic: {'set ✅' if UI['start_pic'] else 'not set'}\n"
                             f"✨ Premium button icons: {len(UI['emojis'])} set "
                             f"({'enabled' if UI['icons'] else 'disabled'})", [
            [B("🖼 Set Start Pic", "op:pic", G), B("🗑 Remove Pic", "op:picdel", R)],
            [B("✨ Button Emojis", "op:emo", P), B("👑 Owners", "op:own", G, "addowner")],
            [B(f"🎨 Icons: {'ON' if UI['icons'] else 'OFF'}", "op:icons", G if UI['icons'] else R)],
            BACK,
        ])
    if d == "op:own":
        extra = set(EXTRA_OWNERS)
        lines = ["👑 **Owners**\n"] + [f"• `{o}`" + ("" if o in extra else " (from settings, fixed)")
                                       for o in config.OWNER_IDS]
        rows = [[B(f"🗑 Remove {o}", f"op:owrm:{o}", R)] for o in EXTRA_OWNERS]
        return await edit(q, "\n".join(lines), [[B("➕ Add Owner", "op:owadd", G)]] + rows + [back("owner")])
    if d == "op:owadd":
        STATE[uid] = {"step": "owner_add"}
        return await edit(q, "👑 Send the **numeric Telegram ID** of the new owner.\n\n/cancel to stop", [back("op:own")])
    if d.startswith("op:owrm:"):
        oid = int(d.split(":")[2])
        if oid in EXTRA_OWNERS:
            set_owner(oid, False)
            await db.set_ui("owners", EXTRA_OWNERS)
            logchan.push(f"Owner REMOVED: {oid} by {uid}", "👑")
        q.data = "op:own"
        return await handle_cb(q)
    if d == "op:pic":
        STATE[uid] = {"step": "start_pic"}
        return await edit(q, "🖼 Send the **photo** for the /start menu.\nIt is saved in the database permanently.\n\n/cancel to stop", [back("owner")])
    if d == "op:picdel":
        UI["start_pic"] = None
        await db.unset_ui("start_pic")
        return await edit(q, "🗑 Start pic removed.", [back("owner")])
    if d == "op:icons":
        UI["icons"] = not UI["icons"]
        await db.set_ui("icons", UI["icons"])
        q.data = "owner"
        return await handle_cb(q)
    if d == "op:emo":
        rows, row = [], []
        for k, label in EMOJI_KEYS.items():
            row.append(B(f"{'✅' if UI['emojis'].get(k) else '➖'} {label}", f"emo:{k}", G if UI['emojis'].get(k) else D))
            if len(row) == 2:
                rows.append(row); row = []
        if row:
            rows.append(row)
        rows += [[B("🗑 Clear all icons", "emo!clear", R)], back("owner")]
        return await edit(q, "✨ **Premium Button Emojis**\nTap a button, then send a premium (custom) emoji "
                             "or its numeric ID. It becomes the icon of that button.", rows)
    if d == "emo!clear":
        UI["emojis"] = {}
        await db.set_ui("emojis", {})
        return await edit(q, "🗑 All button icons cleared.", [back("op:emo")])
    if d.startswith("emo:"):
        key = d[4:]
        if key not in EMOJI_KEYS:
            return
        STATE[uid] = {"step": "emoji", "key": key}
        return await edit(q, f"✨ Send a **premium emoji** (or its ID) for **{EMOJI_KEYS[key]}**.\n"
                             "Send `0` to remove it.\n\n/cancel to stop", [back("op:emo")])

    # ---- custom panel
    if d == "custom":
        return await edit(q, "⚙️ **Custom Panel**\nWhat do you want to set?", [
            [B("👤 Set User", "k:user", P)],
            [B("📢 Set Channel", "k:channel", P)],
            [B("👥 Set Group", "k:group", P)],
            BACK,
        ])

    if d.startswith("k:"):
        kind = d[2:]
        if kind not in db.KINDS:
            return
        s = (await db.get_settings())[kind]
        view = "\n".join(
            f"• {FIELD_LABEL[f]}: " + ("🖼 set" if f == "pfp" and s.get(f) else f"`{s.get(f)}`" if s.get(f) else "—")
            for f in db.FIELDS)
        return await edit(q, f"{KIND_LABEL[kind]} **settings**\n\n{view}", [
            [B("✏️ Set Name", f"f:{kind}:name", P, "edit"), B("📝 Set Bio", f"f:{kind}:bio", P)],
            [B("🖼 Set PFP", f"f:{kind}:pfp", P), B("🔤 Set Username", f"f:{kind}:username", P)],
            [B("🗑 Clear all", f"clr:{kind}", R, "remove")],
            back("custom"),
        ])

    if d.startswith("f:"):
        parts = d.split(":")
        if len(parts) != 3 or parts[1] not in db.KINDS or parts[2] not in db.FIELDS:
            return
        _, kind, field = parts
        STATE[uid] = {"step": "field", "kind": kind, "field": field}
        hint = {
            "name": "Send the new **name**." + (" Use `{n}` for numbers." if kind != "user" else ""),
            "bio": "Send the new **bio** (user max 70 chars, channel/group max 255).",
            "pfp": "Send the **photo** to use.",
            "username": "Send the base **username** (without @). Bot will try name, name1, name2…",
        }[field]
        return await edit(q, f"{KIND_LABEL[kind]} → {hint}\n\n/cancel to stop", [back(f"k:{kind}")])

    if d.startswith("clr:"):
        kind = d[4:]
        if kind not in db.KINDS:
            return
        await db.clear_kind(kind)
        return await edit(q, f"🗑 {KIND_LABEL[kind]} settings cleared.", [back(f"k:{kind}")])

    # ---- add account
    if d == "add":
        await drop_state(uid)
        return await edit(q, "➕ **Add Account**\nHow do you want to log in?", [
            [B("🔑 String Session", "add:str", G)],
            [B("📱 Phone Login", "add:phone", G)],
            BACK,
        ])
    if d == "add:str":
        STATE[uid] = {"step": "string"}
        return await edit(q, "🔑 Send the **string session** (Pyrogram or Telethon).\n\n/cancel to stop", [BACK])
    if d == "add:phone":
        STATE[uid] = {"step": "phone"}
        return await edit(q, "📱 Send the **phone number** with country code, e.g. `+919876543210`\n\n/cancel to stop", [BACK])

    # ---- accounts
    if d == "accs":
        accs = await db.all_accounts()
        if not accs:
            return await edit(q, "No accounts yet.", [[B("➕ Add Account", "add", G, "add")], BACK])
        rows = []
        for a in accs[:40]:
            info = a.get("info", {})
            label = str(info.get("name") or a["_id"])[:20]
            mark = "✅" if a.get("applied") else "⏸"
            rows.append([B(f"{mark} {label}", f"acc:{a['_id']}", P), B("♻️ Reset", f"rst?:{a['_id']}", R, "reset")])
        rows += [[B("♻️ Reset All", "rst?:all", R, "reset"), B("🔗 All Links", "links_all", P, "links")], BACK]
        more = f"\n(showing first 40 of {len(accs)})" if len(accs) > 40 else ""
        return await edit(q, f"👤 **My Accounts** ({len(accs)})\n✅ applied  ⏸ not applied{more}", rows)

    if d.startswith("acc:"):
        acc_id = parse_id(d[4:])
        a = await db.get_account(acc_id) if acc_id else None
        if not a:
            return await edit(q, "Account not found.", [back("accs")])
        info = a.get("info", {})
        ch = a.get("chats", {})
        text = (f"👤 **{info.get('name')}**\nID: `{a['_id']}`\nUsername: @{info.get('username') or '—'}\n"
                f"Phone: {info.get('phone') or '—'}\n"
                f"Channels: {len(ch.get('channel', []))} | Groups: {len(ch.get('group', []))}")
        return await edit(q, text, [
            [B("🚀 Apply", f"ap:{a['_id']}", G, "apply"), B("♻️ Reset", f"rst?:{a['_id']}", R, "reset")],
            [B("🔗 Links", f"lk:{a['_id']}", P, "links"), B("🗑 Remove", f"rm?:{a['_id']}", R, "remove")],
            back("accs"),
        ])

    if d.startswith("lk:"):
        acc_id = parse_id(d[3:])
        a = await db.get_account(acc_id) if acc_id else None
        if not a:
            return await edit(q, "Account not found.", [back("accs")])
        return await send_long(uid, f"🔗 **Links – {a.get('info', {}).get('name')}**", userbot.account_links(a))

    if d == "links_all":
        lines = []
        for a in await db.all_accounts():
            lines.append(f"\n**{a.get('info', {}).get('name')}** ({a['_id']})")
            lines += userbot.account_links(a)
        return await send_long(uid, "🔗 **All Links**", lines)

    if d.startswith("ap:"):
        acc_id = parse_id(d[3:])
        if not acc_id or not await db.get_account(acc_id):
            return await edit(q, "Account not found.", [back("accs")])
        asyncio.create_task(run_job(uid, "Apply", [acc_id], userbot.apply_account))
        return await edit(q, "🚀 Apply started. Progress below 👇", [BACK])

    if d == "apply_all":
        ids = [a["_id"] for a in await db.all_accounts()]
        if not ids:
            return await edit(q, "No accounts yet.", [[B("➕ Add Account", "add", G, "add")], BACK])
        s = await db.get_settings()
        if not any(s[k] for k in db.KINDS):
            return await edit(q, "⚠️ Nothing to apply – set name/bio/pfp/username in ⚙️ Custom first.",
                              [[B("⚙️ Custom", "custom", P, "custom")], BACK])
        asyncio.create_task(run_job(uid, "Apply All", ids, userbot.apply_account))
        return await edit(q, f"🚀 Applying on {len(ids)} accounts. Progress below 👇", [BACK])

    if d.startswith("rst?:"):
        target = d[5:]
        return await edit(q, "♻️ **Reset?**\nOld name, bio, photo & username come back.\n"
                             "Channels/groups get random names & usernames.", [
            [B("✅ Yes, reset", f"rst:{target}", R, "yes"), B("❌ No", "accs", D, "no")]])

    if d.startswith("rst:"):
        target = d[4:]
        if target == "all":
            ids = [a["_id"] for a in await db.all_accounts()]
        else:
            ids = [i for i in [parse_id(target)] if i]
        if not ids:
            return await edit(q, "No accounts to reset.", [back("accs")])
        asyncio.create_task(run_job(uid, "Reset", ids, userbot.reset_account))
        return await edit(q, "♻️ Reset started. Progress below 👇", [BACK])

    if d.startswith("rm?:"):
        return await edit(q, "🗑 Remove this account from the bot?\n(Telegram account itself is not deleted)", [
            [B("✅ Yes", f"rm:{d[4:]}", R, "yes"), B("❌ No", f"acc:{d[4:]}", D, "no")]])
    if d.startswith("rm:"):
        acc_id = parse_id(d[3:])
        if acc_id:
            await db.remove_account(acc_id)
            logchan.push(f"Account removed: {acc_id}", "🗑")
        return await edit(q, "🗑 Account removed.", [back("accs")])


# ------------------------------------------------------------ text / photo input
async def finish_login(m: Message, user_id: int, session: str, info: dict, original: dict):
    existed = await db.get_account(user_id)
    await db.save_account(user_id, session, info, original)
    s = await db.get_settings()
    word = "updated" if existed else "added"
    logchan.push(f"Account {word}: {info.get('name')} ({user_id}) phone {info.get('phone') or '-'}", "➕")
    await reply(m, f"✅ **Account {word}:** {info.get('name')} (`{user_id}`)",
                [[B("🚀 Apply now", f"ap:{user_id}", G, "apply")], BACK])
    if s["auto"]:
        asyncio.create_task(run_job(m.chat.id, "Auto Apply", [user_id], userbot.apply_account))


@bot.on_message(filters.private & OWNER & ~filters.command(["start", "menu", "cancel"]))
async def inputs(_, m: Message):
    try:
        await handle_input(m)
    except Exception as e:
        log.exception("input failed")
        logchan.push(f"Input error: {friendly(e)}", "❌")
        await drop_state(m.from_user.id)
        try:
            await reply(m, f"⚠️ **Error:** {friendly(e)}", [BACK])
        except Exception:
            pass


def custom_emoji_id(m: Message) -> str | None:
    for ent in (m.entities or []) + (m.caption_entities or []):
        if ent.type == MessageEntityType.CUSTOM_EMOJI and ent.custom_emoji_id:
            return str(ent.custom_emoji_id)
    sid = getattr(m.sticker, "custom_emoji_id", None) if m.sticker else None
    if sid:
        return str(sid)
    return None


async def handle_input(m: Message):
    uid = m.from_user.id
    st = STATE.get(uid)
    if not st:
        return await reply(m, "Use the menu 👇", [[B("🏠 Open Menu", "home", P, "back")]])
    step = st["step"]

    # ---- owner panel inputs
    if step == "start_pic":
        if not m.photo:
            return await m.reply("Please send a **photo** (not a file). /cancel to stop")
        UI["start_pic"] = m.photo.file_id
        await db.set_ui("start_pic", m.photo.file_id)
        STATE.pop(uid, None)
        return await reply(m, "✅ Start pic saved permanently.", [back("owner")])

    if step == "owner_add":
        t = (m.text or "").strip()
        if not t.isdigit():
            return await m.reply("❌ Send the numeric ID only. /cancel to stop")
        STATE.pop(uid, None)
        if int(t) not in config.OWNER_IDS:
            set_owner(int(t), True)
            await db.set_ui("owners", EXTRA_OWNERS)
            logchan.push(f"Owner ADDED: {t} by {uid}", "👑")
            try:
                await send(int(t), "👑 You are now an **owner** of this bot. Send /start.")
            except Exception:
                pass
        return await reply(m, f"✅ `{t}` is now an owner.", [back("op:own")])

    if step == "emoji":
        key = st["key"]
        eid = custom_emoji_id(m)
        if not eid and m.text and m.text.strip().isdigit():
            eid = m.text.strip()
        if not eid:
            return await m.reply("❌ That's not a premium emoji. Send a custom emoji or its numeric ID.")
        if eid == "0":
            UI["emojis"].pop(key, None)
        else:
            UI["emojis"][key] = eid
            UI["icons"] = True
            await db.set_ui("icons", True)
        await db.set_ui("emojis", UI["emojis"])
        STATE.pop(uid, None)
        return await reply(m, f"✅ Icon for **{EMOJI_KEYS[key]}** {'removed' if eid == '0' else 'saved'}.",
                           [back("op:emo")])

    # ---- settings
    if step == "field":
        kind, field = st["kind"], st["field"]
        if field == "pfp":
            if not m.photo:
                return await m.reply("Please send a photo.")
            try:
                mem = await bot.download_media(m.photo.file_id, in_memory=True)
                data = bytes(mem.getbuffer())
                if not data:
                    raise ValueError("downloaded photo is empty")
                await db.set_field(kind, "pfp", data)
            except Exception as e:
                log.exception("pfp save failed")
                logchan.push(f"Save {kind} PFP failed: {friendly(e)}", "❌")
                return await reply(m, f"❌ **PFP not saved.**\nReason: {friendly(e)}\nSend the photo again or /cancel.",
                                   [back(f"k:{kind}")])
            shown = "🖼 photo"
        else:
            if not m.text:
                return await m.reply("Please send text.")
            val = m.text.strip()
            limit = {"name": 64 if kind == "user" else 128, "bio": 70 if kind == "user" else 255}.get(field)
            if limit and len(val) > limit:
                return await m.reply(f"❌ Too long ({len(val)}/{limit}). Send a shorter one.")
            if field == "username":
                val = val.lstrip("@")
                if not (4 <= len(val) <= 28) or not val.replace("_", "").isalnum() or not val[0].isalpha() \
                        or not val.isascii():
                    return await m.reply("❌ Invalid username. 4–28 chars, English letters/numbers/_ , start with a letter.")
            try:
                await db.set_field(kind, field, val)
            except Exception as e:
                log.exception("save field failed")
                logchan.push(f"Save {kind}.{field} failed: {friendly(e)}", "❌")
                return await reply(m, f"❌ **{FIELD_LABEL[field]} not saved.**\nReason: {friendly(e)}\n"
                                      "Send it again or /cancel.", [back(f"k:{kind}")])
            shown = f"`{val}`"
        STATE.pop(uid, None)
        saved = (await db.get_settings()).get(kind, {}).get(field)
        if not saved:
            return await reply(m, f"❌ {FIELD_LABEL[field]} could not be verified in database. Try again.",
                               [back(f"k:{kind}")])
        logchan.push(f"Setting saved: {kind}.{field} = {shown}", "⚙️")
        return await reply(m, f"✅ **{KIND_LABEL[kind]} {FIELD_LABEL[field]} saved:** {shown}\n\n"
                              "It will be used on all accounts (tap Apply All, or keep Auto ON).",
                           [[B("🔙 Back", f"k:{kind}", D, "back"), B("🚀 Apply All", "apply_all", G, "apply")]])

    if not m.text:
        return await m.reply("Please send text. /cancel to stop")
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
            user_id, session, info, original = await asyncio.wait_for(userbot.login_with_string(text), 90)
        except asyncio.TimeoutError:
            return await wait.edit_text("❌ Session check timed out. Try again.")
        except Exception as e:
            await wait.delete()
            return await reply(m, f"❌ Invalid session: {friendly(e)}", [BACK])
        await wait.delete()
        return await finish_login(m, user_id, session, info, original)

    # ---- phone login
    if step == "phone":
        phone = "".join(ch for ch in text if ch.isdigit() or ch == "+")
        if len(phone.lstrip("+")) < 7:
            return await m.reply("❌ That doesn't look like a phone number. Example: `+919876543210`")
        c = userbot.new_client(None, "login")
        try:
            await c.connect()
            sent = await c.send_code(phone)
        except PhoneNumberInvalid:
            await _disc(c)
            return await m.reply("❌ Invalid phone number. Try again or /cancel.")
        except FloodWait as e:
            await _disc(c)
            STATE.pop(uid, None)
            return await reply(m, f"❌ Too many tries. Wait {e.value}s.", [BACK])
        except Exception as e:
            await _disc(c)
            STATE.pop(uid, None)
            return await reply(m, f"❌ {friendly(e)}", [BACK])
        STATE[uid] = {"step": "code", "client": c, "phone": phone, "hash": sent.phone_code_hash, "t": time.time()}
        return await m.reply("📩 OTP sent. Send it **with spaces**, e.g. `1 2 3 4 5`\n/cancel to stop")

    if step == "code":
        c = st["client"]
        code = "".join(ch for ch in text if ch.isdigit())
        try:
            await m.delete()
        except Exception:
            pass
        if len(code) < 5:
            return await m.reply("❌ Code should be 5+ digits. Send again or /cancel.")
        try:
            await c.sign_in(st["phone"], st["hash"], code)
        except SessionPasswordNeeded:
            st["step"] = "password"
            return await m.reply("🔐 2-step verification is on. Send the **password**.\n/cancel to stop")
        except PhoneCodeInvalid:
            return await m.reply("❌ Wrong code. Send it again or /cancel.")
        except PhoneCodeExpired:
            await drop_state(uid)
            return await reply(m, "❌ Code expired. Start phone login again.", [[B("📱 Phone Login", "add:phone", G)], BACK])
        except Exception as e:
            await drop_state(uid)
            return await reply(m, f"❌ Login failed: {friendly(e)}", [BACK])
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
            await drop_state(uid)
            return await reply(m, f"❌ Login failed: {friendly(e)}", [BACK])
        return await _phone_done(m, uid, c)


async def _disc(c: Client):
    try:
        await c.disconnect()
    except Exception:
        pass


async def _phone_done(m: Message, uid: int, c: Client):
    STATE.pop(uid, None)
    try:
        session = await c.export_session_string()
        me = await c.get_me()
        original = await userbot.snapshot_profile(c)
    finally:
        await _disc(c)
    await finish_login(m, me.id, session, userbot.user_info(me), original)


# ------------------------------------------------------------ housekeeping
async def expire_logins():
    """Close half-finished phone logins after 10 minutes."""
    while True:
        await asyncio.sleep(60)
        for uid, st in list(STATE.items()):
            if st.get("client") and time.time() - st.get("t", time.time()) > 600:
                await drop_state(uid)
                try:
                    await bot.send_message(uid, "⌛ Login timed out. Start again from ➕ Add Account.")
                except Exception:
                    pass


# ------------------------------------------------------------ run
async def main():
    try:
        await db.ping()
    except Exception as e:
        raise SystemExit(f"MongoDB connection failed: {e}")
    ui = await db.get_ui()
    clean = {k: str(v) for k, v in dict(ui["emojis"]).items() if k in EMOJI_KEYS and str(v).isdigit()}
    if clean != dict(ui["emojis"]):
        await db.set_ui("emojis", clean)
        log.warning("Removed invalid button emoji IDs from DB")
    UI.update(emojis=clean, icons=bool(ui["icons"]), start_pic=ui["start_pic"])
    for o in ui.get("owners") or []:
        set_owner(int(o), True)
    await bot.start()
    logchan.setup(bot)
    me = await bot.get_me()
    log.info("Bot started as @%s – owners: %s", me.username, config.OWNER_IDS)
    asyncio.create_task(expire_logins())
    shop.start_background()
    logchan.push(f"Bot started as @{me.username}", "🟢")
    for oid in config.OWNER_IDS:
        try:
            await send(oid, "✅ Bot is online.", [[B("🏠 Open Menu", "home", P, "back")]])
        except Exception:
            pass
    await idle()
    await bot.stop()


if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(main())
