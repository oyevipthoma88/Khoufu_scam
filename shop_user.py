"""Buyer side: must-join check, menus, ordering, payment, delivery, OTP, logout, ownership."""
import asyncio
import datetime as dt
import time

from bson import ObjectId
from bson.errors import InvalidId
from pyrogram.errors import FloodWait, UserNotParticipant
from pyrogram.enums import ChatMemberStatus
from pyrogram.types import CallbackQuery, Message

import config
import database as db
import shop_tg
import ui
from shop_core import (KIND_ICON, KIND_NAME, SSTATE, STATUS_TXT, B, D, G, P, R, build, clean, clear_state,
                       friendly_err, is_owner, lg, lock, money, notify_owners, order_line, price_for_country,
                       price_of, reply, retry, send, show, who)
import shop_core as core

TUTORIAL = """📖 **How to buy**

1️⃣ Pick what you want: 📱 Account, 📢 Channel, 👥 Group, 🤖 Bot or 📚 Method.
2️⃣ Choose the item (for accounts: choose the country).
3️⃣ Pay using the QR / UPI shown, then tap **✅ I have paid** and send the screenshot or UTR.
4️⃣ The owner checks the payment. As soon as it is approved you get your item here.

📱 **Account:** you get the number + 2FA. Log in on Telegram, tap **📩 Get OTP** and I send the code with spaces. After you are logged in tap **✅ I'm logged in** – I log my side out so only you have it.

📢 **Channel / 👥 Group:** you get a join link. Join, tap **✅ Yes, I joined** and I transfer the ownership to you. To get it on another account: tap **🆔 Use another ID**, join with that account and send its numeric ID.

🎟 **Redeem Code** adds balance you can pay with.
Orders not paid within {mins} minutes are cancelled automatically."""

_ok_until: dict[int, float] = {}
_warned_force: dict = {}


# ================================================================ must-join
async def _force_list() -> list[dict]:
    shop = await db.get_shop()
    out = list(shop["force"])
    for ref in config.FORCE_JOIN:  # env channels (no stored link)
        r = int(ref) if ref.lstrip("-").isdigit() else ref.lstrip("@")
        if not any(str(f["chat"]) == str(r) for f in out):
            link = f"https://t.me/{r}" if isinstance(r, str) else ""
            out.append({"chat": r, "title": str(r), "link": link})
    return out


async def missing_channels(uid: int, use_cache: bool = True) -> list[dict]:
    if use_cache and _ok_until.get(uid, 0) > time.time():
        return []
    miss = []
    for ch in await _force_list():
        try:
            m = await retry(core.BOT.get_chat_member, ch["chat"], uid)
            if m.status in (ChatMemberStatus.LEFT, ChatMemberStatus.BANNED):
                miss.append(ch)
        except UserNotParticipant:
            miss.append(ch)
        except Exception as e:  # bot not admin / chat gone: don't lock everybody out, tell the owners once
            key = str(ch["chat"])
            if time.time() - _warned_force.get(key, 0) > 3600:
                _warned_force[key] = time.time()
                lg(f"Must-join check failed for {ch['title']} ({ch['chat']}): {friendly_err(e)} – "
                   "make the bot ADMIN there. Users are let through meanwhile.", "⚠️")
                await notify_owners(f"⚠️ Must-join check failed for **{clean(ch['title'])}**: {friendly_err(e)}\n"
                                    "Add the bot as admin in that channel. Until then users are not blocked.")
    if not miss:
        _ok_until[uid] = time.time() + 300
    return miss


def gate_view(miss: list[dict]):
    text = "🔒 **Please join these channels first**\n\nJoin all of them, then tap **✅ Verify**."
    rows = []
    for ch in miss:
        if ch.get("link"):
            rows.append([ui.U(f"📢 {ch['title'][:40]}", ch["link"])])
    rows.append([B("✅ Verify", "u:verify", G)])
    return text, rows


# ================================================================ menus
async def home_view(uid: int, name: str):
    u = await db.get_user(uid) or {}
    shop = await db.get_shop()
    text = (f"🛍 **Welcome, {clean(name)}!**\n\n💰 Balance: **{money(u.get('balance', 0))}**\n"
            "What would you like to do?")
    upd = shop.get("update_link") or ""
    con = shop.get("contact") or ""
    rows = [
        [B("📱 Buy Account", "u:buy:account", G, "u_acc"), B("📢 Buy Channel", "u:buy:channel:0", G, "u_chan")],
        [B("👥 Buy Group", "u:buy:group:0", G, "u_grp"), B("🤖 Buy Bot", "u:buy:bot:0", G, "u_bot")],
        [B("📚 Buy Method", "u:buy:method:0", G, "u_meth"),
         ui.U("👑 Owner", _tg_link(con)) if con else B("👑 Owner", "u:owner", P, "u_owner")],
        [ui.U("📣 Update Channel", _tg_link(upd)) if upd else B("📣 Update Channel", "u:upd", P, "u_upd"),
         B("📖 User Tutorial", "u:tut", D, "u_tut")],
        [B("🎟 Redeem Code", "u:redeem", P, "u_redeem"), B("🧾 My Orders", "u:orders", D, "u_orders")],
    ]
    if is_owner(uid):
        rows.append([B("🔙 Owner menu", "home", D, "back")])
    return text, rows


def _tg_link(v: str) -> str:
    v = v.strip()
    if v.startswith("http"):
        return v
    if v.lstrip("-").isdigit():
        return f"tg://user?id={v}"
    return f"https://t.me/{v.lstrip('@')}"


async def cmd_start(_, m: Message):
    u = await db.touch_user(m.from_user)
    await clear_state(m.from_user.id)
    uid = m.from_user.id
    if not is_owner(uid):
        miss = await missing_channels(uid, use_cache=False)
        if miss:
            t, r = gate_view(miss)
            return await reply(m, t, r)
        if not u.get("verified"):
            await db.set_user(uid, {"verified": True})
            lg(f"New user verified: {who(m.from_user)}", "👤")
    t, r = await home_view(uid, m.from_user.first_name or "friend")
    await reply(m, t, r)


# ================================================================ callbacks
async def user_cb(q: CallbackQuery):
    d, uid = q.data, q.from_user.id
    try:
        await q.answer()
    except Exception:
        pass
    if core.too_fast(uid):
        return
    await db.touch_user(q.from_user)

    if d == "u:verify":
        miss = await missing_channels(uid, use_cache=False)
        if miss:
            t, r = gate_view(miss)
            names = ", ".join(c["title"] for c in miss)[:150]
            try:
                await q.answer(f"❌ Not joined yet: {names}", show_alert=True)
            except Exception:
                pass
            return await show(q, t, r)
        await db.set_user(uid, {"verified": True})
        lg(f"User verified: {who(q.from_user)}", "✅")
        d = "u:home"

    if not is_owner(uid):
        miss = await missing_channels(uid)
        if miss:
            t, r = gate_view(miss)
            return await show(q, t, r)

    parts = d.split(":")
    act = parts[1]

    if act == "home":
        await clear_state(uid)
        t, r = await home_view(uid, q.from_user.first_name or "friend")
        return await show(q, t, r)
    if act == "tut":
        shop = await db.get_shop()
        txt = shop.get("tutorial") or TUTORIAL.format(mins=config.ORDER_TIMEOUT)
        return await show(q, txt, [[B("🔙 Back", "u:home", D, "back")]])
    if act == "owner":
        return await show(q, "👑 Owner contact is not set yet.", [[B("🔙 Back", "u:home", D, "back")]])
    if act == "upd":
        return await show(q, "📣 Update channel is not set yet.", [[B("🔙 Back", "u:home", D, "back")]])
    if act == "buy":
        return await buy_list(q, parts)
    if act == "acc":
        return await item_view(q, "a", parts[2])
    if act == "item":
        return await item_view(q, "i", parts[2])
    if act == "go":
        return await make_order(q, parts[2], parts[3])
    if act == "orders":
        return await orders_view(q)
    if act == "ord":
        return await order_view(q, int(parts[2]))
    if act == "redeem":
        SSTATE[uid] = {"step": "redeem"}
        return await show(q, "🎟 Send your **redeem code**.\n\n/cancel to stop", [[B("🔙 Back", "u:home", D, "back")]])
    if act in ("paid", "bal", "cx", "otp", "done", "lo", "lof", "joined", "other", "chk"):
        return await order_action(q, act, int(parts[2]))


# ---------------------------------------------------------------- browsing
PAGE = 8


async def buy_list(q: CallbackQuery, parts: list[str]):
    kind = parts[2]
    shop = await db.get_shop()
    back = [B("🔙 Back", "u:home", D, "back")]
    if kind == "account":
        rows_c = await db.account_countries()
        if not rows_c:
            return await show(q, "😔 **Accounts are out of stock** right now.\nCheck again later or tap 📣 Update Channel.", [back])
        lines, rows = ["📱 **Account stock**\n"], []
        for c in rows_c:
            cc = c["_id"]
            p = price_for_country(shop, cc)
            label = f"{shop_tg.flag(cc)} {c.get('name') or cc}"
            lines.append(f"{label} – **{c['n']}** in stock – {money(p) if p else 'price not set'}")
            if p:
                rows.append([B(f"{label} • {c['n']} • {money(p)}", f"u:acc:{cc}", G)])
        lines.append("\nSelect a country 👇")
        return await show(q, "\n".join(lines), rows + [back])
    page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
    items = await db.stock_find({"kind": kind, "status": "available"}, limit=PAGE + 1, skip=page * PAGE)
    nxt = len(items) > PAGE
    items = items[:PAGE]
    if not items and page == 0:
        return await show(q, f"😔 **No {KIND_NAME[kind].lower()}s in stock** right now.", [back])
    rows = []
    for it in items:
        p = price_of(shop, it)
        if p:
            rows.append([B(f"{KIND_ICON[kind]} {it['title'][:34]} • {money(p)}", f"u:item:{it['_id']}", G)])
    nav = []
    if page > 0:
        nav.append(B("⬅️ Prev", f"u:buy:{kind}:{page - 1}", D))
    if nxt:
        nav.append(B("➡️ Next", f"u:buy:{kind}:{page + 1}", D))
    if nav:
        rows.append(nav)
    if not rows:
        return await show(q, "😔 Nothing with a price is in stock yet.", [back])
    return await show(q, f"{KIND_ICON[kind]} **{KIND_NAME[kind]}s for sale**\nTap one to see details 👇", rows + [back])


async def item_view(q: CallbackQuery, t: str, ref: str):
    shop = await db.get_shop()
    if t == "a":
        n = await db.stock_count({"kind": "account", "country": ref, "status": "available"})
        sample = await db.stock_col.find_one({"kind": "account", "country": ref, "status": "available"})
        if not n or not sample:
            return await show(q, "😔 Sorry, that country just went out of stock.", [[B("🔙 Back", "u:buy:account", D, "back")]])
        p = price_for_country(shop, ref)
        text = (f"📱 **Telegram account**\n{shop_tg.flag(ref)} Country: **{sample.get('country_name') or ref}**\n"
                f"📦 In stock: **{n}**\n💰 Price: **{money(p)}**\n\n"
                "You get the number + 2FA, and I give you the login OTP here.")
        back_to = "u:buy:account"
    else:
        try:
            it = await db.get_stock(ObjectId(ref))
        except InvalidId:
            it = None
        if not it or it["status"] != "available":
            return await show(q, "😔 Sorry, this item was just sold.", [[B("🔙 Back", "u:home", D, "back")]])
        p = price_of(shop, it)
        extra = f"\n👥 Members: **{it['members']}**" if it.get("members") else ""
        desc = f"\n\n{clean(it.get('desc'))}" if it.get("desc") else ""
        text = f"{KIND_ICON[it['kind']]} **{clean(it['title'])}**{extra}\n💰 Price: **{money(p)}**{desc}"
        back_to = f"u:buy:{it['kind']}:0"
    if p <= 0:
        return await show(q, "⚠️ Price is not set for this item yet. Contact the owner.", [[B("🔙 Back", back_to, D, "back")]])
    return await show(q, text, [[B(f"💳 Buy now – {money(p)}", f"u:go:{t}:{ref}", G)], [B("🔙 Back", back_to, D, "back")]])


# ---------------------------------------------------------------- ordering
async def cancel_order(o: dict, status: str = "cancelled") -> bool:
    done = await db.move_order(o["_id"], ["awaiting_payment"], status)
    if done:
        await db.release_stock(o["stock_id"])
    return bool(done)


async def make_order(q: CallbackQuery, t: str, ref: str):
    uid = q.from_user.id
    shop = await db.get_shop()
    back = [[B("🔙 Back", "u:home", D, "back")]]
    async with lock(f"mk{uid}"):  # double tap on "Buy now" must not create two orders
        for o in [x async for x in db.orders_col.find({"user_id": uid, "status": "awaiting_payment"})]:
            await cancel_order(o, "cancelled")
        if t == "a":
            query = {"kind": "account", "country": ref}
            price = price_for_country(shop, ref)
        else:
            try:
                query = {"_id": ObjectId(ref)}
            except InvalidId:
                return await show(q, "⚠️ Invalid item.", back)
            it = await db.get_stock(query["_id"])
            price = price_of(shop, it) if it else 0
        if price <= 0:
            return await show(q, "⚠️ Price is not set for this item yet. Contact the owner.", back)
        bal = float((await db.get_user(uid) or {}).get("balance", 0))
        if not (shop.get("qr") or shop.get("upi")) and bal < price:
            lg("Order blocked: payment method (QR/UPI) is not set", "⚠️")
            await notify_owners("⚠️ A buyer tried to purchase but **no QR / UPI is set**. Shop Admin → Set QR.")
            return await show(q, "⚠️ Payments are not set up yet. Please contact the owner.", back)
        item = await db.reserve_stock(query, uid)
        if not item:
            return await show(q, "😔 Sorry, someone just bought it. Please pick another.", back)
        o = await db.new_order({
            "user_id": uid, "user_name": q.from_user.first_name or "", "username": q.from_user.username,
            "kind": item["kind"], "stock_id": item["_id"], "country": item.get("country"),
            "title": item.get("title") or f"{item.get('country_name', '')} account", "price": price,
            "status": "awaiting_payment", "expires": db.now() + dt.timedelta(minutes=config.ORDER_TIMEOUT)})
    lg(f"Order #{o['_id']} created by {who(q.from_user)}: {order_line(o)}", "🛒")
    await pay_screen(q, o, shop, bal)


async def pay_screen(q: CallbackQuery, o: dict, shop: dict, bal: float | None = None):
    if bal is None:
        bal = float((await db.get_user(o["user_id"]) or {}).get("balance", 0))
    pay = []
    if shop.get("upi"):
        pay.append(f"🏦 UPI: `{shop['upi']}`")
    if shop.get("qr"):
        pay.append("📷 Scan the QR code")
    text = (f"💳 **Payment – Order #{o['_id']}**\n\n{KIND_ICON[o['kind']]} {clean(o['title'])}\n"
            f"💰 Amount: **{money(o['price'])}**\n\n" + ("\n".join(pay) + "\n\n" if pay else "") +
            f"1. Pay exactly **{money(o['price'])}**\n2. Tap **✅ I have paid**, send screenshot / UTR\n\n"
            f"⏳ Unpaid orders are cancelled after {config.ORDER_TIMEOUT} min.")
    rows = [[B("✅ I have paid", f"u:paid:{o['_id']}", G)]]
    if bal >= o["price"]:
        rows.insert(0, [B(f"💰 Pay with balance ({money(bal)})", f"u:bal:{o['_id']}", P)])
    rows.append([B("❌ Cancel order", f"u:cx:{o['_id']}", R)])
    await show(q, text, rows, photo=shop.get("qr"))


async def get_my_order(q_or_uid, oid: int):
    uid = q_or_uid if isinstance(q_or_uid, int) else q_or_uid.from_user.id
    o = await db.get_order(oid)
    return o if o and o["user_id"] == uid else None


async def orders_view(q: CallbackQuery):
    orders = await db.user_orders(q.from_user.id, 8)
    if not orders:
        return await show(q, "🧾 You have no orders yet.", [[B("🔙 Back", "u:home", D, "back")]])
    rows = [[B(f"{order_line(o)[:40]} • {STATUS_TXT.get(o['status'], o['status'])[:14]}", f"u:ord:{o['_id']}", D)]
            for o in orders]
    return await show(q, "🧾 **Your orders**", rows + [[B("🔙 Back", "u:home", D, "back")]])


async def order_view(q: CallbackQuery, oid: int):
    o = await get_my_order(q, oid)
    back = [B("🔙 Orders", "u:orders", D, "back")]
    if not o:
        return await show(q, "Order not found.", [back])
    st = o["status"]
    if st == "awaiting_payment":
        return await pay_screen(q, o, await db.get_shop())
    if st == "delivered":
        text, rows = await delivery_view(o)
        return await show(q, text, rows)
    return await show(q, f"🧾 **Order #{oid}**\n{order_line(o)}\nStatus: {STATUS_TXT.get(st, st)}", [back])


async def order_action(q: CallbackQuery, act: str, oid: int):
    uid = q.from_user.id
    o = await get_my_order(q, oid)
    back = [[B("🏠 Menu", "u:home", D, "back")]]
    if not o:
        return await show(q, "Order not found.", back)

    if act == "cx":
        if await cancel_order(o, "cancelled"):
            lg(f"Order #{oid} cancelled by buyer", "🚫")
            return await show(q, "🚫 Order cancelled.", back)
        return await show(q, "This order can't be cancelled any more.", back)

    if act == "paid":
        if o["status"] != "awaiting_payment":
            return await show(q, f"This order is: {STATUS_TXT.get(o['status'], o['status'])}", back)
        await db.set_order(oid, {"expires": db.now() + dt.timedelta(minutes=30)})
        SSTATE[uid] = {"step": "proof", "oid": oid}
        return await show(q, f"📸 Send the **payment screenshot** (photo) or the **UTR / transaction ID** as text.\n"
                             f"Amount: **{money(o['price'])}** • Order #{oid}\n\n/cancel to stop",
                          [[B("❌ Cancel order", f"u:cx:{oid}", R)]])

    if act == "bal":
        if o["status"] != "awaiting_payment":
            return await show(q, f"This order is: {STATUS_TXT.get(o['status'], o['status'])}", back)
        if not await db.take_balance(uid, o["price"]):
            return await show(q, "💰 Not enough balance.", back)
        o2 = await db.move_order(oid, ["awaiting_payment"], "approved", {"paid_by": "balance"})
        if not o2:  # expired in the meantime -> give the money back
            await db.add_balance(uid, o["price"])
            return await show(q, "⌛ Order expired. Balance was not charged.", back)
        lg(f"Order #{oid} paid with balance by {who(q.from_user)}", "💰")
        await show(q, "✅ Paid from balance. Delivering…", back)
        return await deliver(o2)

    if act == "otp":
        return await otp_flow(q, o)
    if act == "done":
        if o["status"] != "delivered":
            return await show(q, "Nothing to do for this order.", back)
        return await show(q, "✅ **Logged in on your own device?**\n\nIf yes, I will log out **my** session of this account "
                             "so only you have access. After that I can't send OTPs anymore.",
                          [[B("✅ Yes, logout now", f"u:lo:{oid}", G)], [B("🔙 Not yet", f"u:ord:{oid}", D)]])
    if act in ("lo", "lof"):
        return await logout_flow(q, o, force=(act == "lof"))
    if act == "joined":
        SSTATE.pop(uid, None)
        return await claim_flow(q, o)
    if act == "chk":
        return await claim_flow(q, o)
    if act == "other":
        if o["status"] != "delivered":
            return await show(q, "Nothing to do for this order.", back)
        SSTATE[uid] = {"step": "target", "oid": oid}
        return await show(q, "🆔 **Transfer to another account**\n\n1. Join the channel/group with that account\n"
                             "2. Send me its **numeric Telegram ID** here (e.g. from @userinfobot)\n\n/cancel to stop",
                          [[B("🔙 Back", f"u:ord:{oid}", D)]])


# ---------------------------------------------------------------- user text input
async def user_input(m: Message, st: dict):
    uid = m.from_user.id
    step = st["step"]

    if step == "proof":
        oid = st["oid"]
        o = await get_my_order(uid, oid)
        if not o or o["status"] != "awaiting_payment":
            SSTATE.pop(uid, None)
            return await reply(m, "This order is not waiting for payment any more.", [[B("🏠 Menu", "u:home", D)]])
        proof_file = m.photo.file_id if m.photo else None
        proof_txt = (m.text or m.caption or "").strip()
        if not proof_file and len(proof_txt) < 6:
            return await m.reply("Please send a **screenshot** (photo) or the UTR / transaction ID (6+ characters).")
        o2 = await db.move_order(oid, ["awaiting_payment"], "awaiting_approval",
                                 {"proof_file": proof_file, "proof_text": proof_txt[:200],
                                  "expires": db.now() + dt.timedelta(days=3)})
        SSTATE.pop(uid, None)
        if not o2:
            return await reply(m, "⌛ This order expired. Please order again.", [[B("🏠 Menu", "u:home", D)]])
        lg(f"Payment proof for order #{oid} from {who(m.from_user)}", "🧾")
        await reply(m, f"✅ **Payment submitted** for order #{oid}.\nThe owner will verify it shortly – "
                       "you'll get a message here as soon as it's approved.", [[B("🏠 Menu", "u:home", D)]])
        txt = (f"🧾 **New payment – Order #{oid}**\n\n👤 {who(m.from_user)}\n{order_line(o2)}"
               + (f"\n🔖 UTR: `{proof_txt}`" if proof_txt else ""))
        await notify_owners(txt, [[B("✅ Approve", f"so:ap:{oid}", G), B("❌ Reject", f"so:rj:{oid}", R)]],
                            photo=proof_file)
        return

    if step == "redeem":
        code = (m.text or "").strip().upper()
        if not code:
            return await m.reply("Please send the code as text.")
        SSTATE.pop(uid, None)
        c = await db.redeem_code(code, uid)
        if not c:
            old = await db.get_code(code)
            why = ("You already used this code." if old and uid in old.get("used_by", [])
                   else "This code is fully used." if old else "Invalid code.")
            return await reply(m, f"❌ {why}", [[B("🎟 Try again", "u:redeem", P), B("🏠 Menu", "u:home", D)]])
        await db.add_balance(uid, c["amount"])
        bal = (await db.get_user(uid) or {}).get("balance", 0)
        lg(f"Redeem {code} (+{money(c['amount'])}) by {who(m.from_user)}", "🎟")
        return await reply(m, f"🎉 **{money(c['amount'])} added!**\n💰 Balance: **{money(bal)}**",
                           [[B("🏠 Menu", "u:home", G)]])

    if step == "target":
        oid = st["oid"]
        txt = (m.text or "").strip()
        if not txt.lstrip("-").isdigit() or int(txt) <= 0:
            return await m.reply("❌ Send the **numeric ID** only (digits). /cancel to stop")
        o = await get_my_order(uid, oid)
        if not o or o["status"] != "delivered":
            SSTATE.pop(uid, None)
            return await m.reply("This order is not waiting for a transfer.")
        await db.set_order(oid, {"target": int(txt)})
        SSTATE.pop(uid, None)
        lg(f"Order #{oid}: ownership target set to {txt}", "🆔")
        msg = await m.reply(f"🔎 Checking ID `{txt}`…")
        text, rows = await run_claim(oid)
        return await ui.safe_send(lambda ic: msg.edit_text(text, reply_markup=build(rows, ic)))


# ================================================================ delivery
def _pw(it: dict) -> str:
    return it.get("password") or "none"


async def delivery_view(o: dict):
    it = await db.get_stock(o["stock_id"]) or {}
    oid = o["_id"]
    if o["kind"] == "account":
        text = (f"✅ **Order #{oid} approved!**\n\n📱 Number: `+{it.get('phone', '?')}`\n🔐 2FA password: `{_pw(it)}`\n\n"
                "**How to login**\n1️⃣ Open Telegram → add account → enter this number\n"
                "2️⃣ Telegram sends a login code → tap **📩 Get OTP** and I send it to you with spaces\n"
                "3️⃣ If asked, enter the 2FA password above\n"
                "4️⃣ When you are really logged in, tap **✅ I'm logged in**\n\n"
                "⚠️ Don't tap *logged in* before you are inside.")
        rows = [[B("📩 Get OTP", f"u:otp:{oid}", G)], [B("✅ I'm logged in", f"u:done:{oid}", P)]]
        return text, rows
    kind = "channel" if o["kind"] == "channel" else "group"
    link = o.get("link") or "(link missing – contact owner)"
    target = o.get("target") or o["user_id"]
    text = (f"✅ **Order #{oid} approved!**\n\n{KIND_ICON[o['kind']]} **{clean(it.get('title') or o['title'])}**\n"
            f"🔗 Join here: {link}\n\nJoin the {kind}, then tap **✅ Yes, I joined**.\n"
            f"I will move the ownership to ID `{target}`.\n"
            "Want it on another account? Tap **🆔 Use another ID**.")
    rows = [[B("✅ Yes, I joined", f"u:joined:{oid}", G)], [B("🆔 Use another ID", f"u:other:{oid}", P)]]
    return text, rows


async def complete_order(o: dict, extra: dict | None = None):
    await db.move_order(o["_id"], ["approved", "delivered", "transferring", "logging_out", "manual"],
                        "completed", extra)
    await db.update_stock(o["stock_id"], {"status": "sold", "sold_to": o["user_id"], "sold_at": db.now()},
                          unset={"session": ""} if o["kind"] == "account" else None)
    lg(f"Order #{o['_id']} COMPLETED – {order_line(o)}", "🏁")


async def deliver(o: dict):
    """Send the purchased item. Safe to call again on 'approved' orders (re-deliver)."""
    oid, uid = o["_id"], o["user_id"]
    try:
        it = await db.get_stock(o["stock_id"])
        if not it:
            raise RuntimeError("stock item missing")
        if o["kind"] == "account":
            await db.move_order(oid, ["approved"], "delivered")
            o["status"] = "delivered"
            text, rows = await delivery_view(o)
            await send(uid, text, rows)
        elif o["kind"] in ("channel", "group"):
            seller = await db.get_seller(it["seller"])
            if not seller:
                raise RuntimeError("seller account was removed – add it again")
            link = await shop_tg.make_join_link(seller["session"], it, f"order {oid}")
            await db.move_order(oid, ["approved"], "delivered", {"link": link})
            o["status"], o["link"] = "delivered", link
            text, rows = await delivery_view(o)
            await send(uid, text, rows)
        elif o["kind"] == "bot":
            await send(uid, f"✅ **Order #{oid} approved!**\n\n🤖 Bot: @{it['bot_username']}\n"
                            f"🔑 Token: `{it['token']}`\n\n⚠️ Open @BotFather → /revoke to get a token only you know.",
                       [[B("🏠 Menu", "u:home", G)]])
            await complete_order(o)
        elif o["kind"] == "method":
            await send(uid, f"✅ **Order #{oid} approved!**\n\n📚 **{clean(it['title'])}**\n\n{it['content']}",
                       [[B("🏠 Menu", "u:home", G)]])
            await complete_order(o)
        lg(f"Order #{oid} delivered to {uid}", "📦")
    except Exception as e:
        core.log.exception("deliver failed")
        lg(f"Delivery FAILED for order #{oid}: {friendly_err(e)}", "❌")
        await notify_owners(f"❌ **Delivery failed – order #{oid}**\n{order_line(o)}\nReason: {friendly_err(e)}\n"
                            "Fix it, then use Shop Admin → Orders → Deliver again.",
                            [[B("📦 Deliver again", f"so:rd:{oid}", G)]])


# ---------------------------------------------------------------- OTP
async def otp_flow(q: CallbackQuery, o: dict):
    oid = o["_id"]
    if o["status"] != "delivered" or o["kind"] != "account":
        return
    lk = lock(f"otp{oid}")
    if lk.locked():
        return await q.answer("⏳ Already checking for your code…", show_alert=False)
    asyncio.create_task(_otp_task(q.message.chat.id, o, lk))


async def _otp_task(chat_id: int, o: dict, lk: asyncio.Lock):
    oid = o["_id"]
    rows = [[B("📩 Get OTP again", f"u:otp:{oid}", G)], [B("✅ I'm logged in", f"u:done:{oid}", P)]]
    async with lk:
        msg = await send(chat_id, "⏳ **Waiting for the login code…**\nRequest it now in your Telegram app "
                                  "(I check for up to 2 minutes).")
        it = await db.get_stock(o["stock_id"]) or {}
        try:
            if not it.get("session"):
                raise RuntimeError("session already logged out")
            code, mid = await shop_tg.wait_for_otp(it["session"], it.get("otp_seen", 0))
        except Exception as e:
            lg(f"OTP error order #{oid}: {friendly_err(e)}", "❌")
            return await _edit(msg, f"❌ Couldn't read the code: {friendly_err(e)}\nTry again or contact the owner.", rows)
        if not code:
            lg(f"OTP not received for order #{oid}", "⌛")
            return await _edit(msg, "⌛ No new code arrived. Request the code in Telegram, then tap **Get OTP again**.", rows)
        await db.update_stock(o["stock_id"], {"otp_seen": mid})
        lg(f"OTP sent for order #{oid}", "📩")
        await _edit(msg, f"📩 **Your OTP:**\n\n`{shop_tg.spaced(code)}`\n\n"
                         f"🔐 2FA (if asked): `{_pw(it)}`\n\nEnter it in Telegram (without spaces).", rows)


async def _edit(msg, text, rows):
    try:
        await retry(ui.safe_send, lambda ic: msg.edit_text(text, reply_markup=build(rows, ic)))
    except Exception:
        await send(msg.chat.id, text, rows)


# ---------------------------------------------------------------- logout
async def logout_flow(q: CallbackQuery, o: dict, force: bool):
    oid = o["_id"]
    back = [[B("🏠 Menu", "u:home", D, "back")]]
    async with lock(f"lo{oid}"):
        o2 = await db.move_order(oid, ["delivered"], "logging_out")
        if not o2:
            return await show(q, "Nothing to do for this order.", back)
        it = await db.get_stock(o["stock_id"]) or {}
        await show(q, "⏳ Checking your login and logging out my session…", [])
        try:
            done, why = await shop_tg.logout_stock(it["session"], force) if it.get("session") else (True, "already")
        except Exception as e:
            await db.move_order(oid, ["logging_out"], "delivered")
            lg(f"Logout error order #{oid}: {friendly_err(e)}", "❌")
            return await show(q, f"❌ Logout failed: {friendly_err(e)}\nTry again.",
                              [[B("🔁 Try again", f"u:lo:{oid}", G)], [B("🔙 Back", f"u:ord:{oid}", D)]])
        if not done:
            await db.move_order(oid, ["logging_out"], "delivered")
            return await show(q, "⚠️ I can't see your login yet – only my own session exists.\n"
                                 "Finish logging in first (tap **Get OTP** if you need the code).",
                              [[B("📩 Get OTP", f"u:otp:{oid}", G)],
                               [B("Logout anyway", f"u:lof:{oid}", R)], [B("🔙 Back", f"u:ord:{oid}", D)]])
        await complete_order(o2)
        return await show(q, f"🎉 **Done! Order #{oid} completed.**\nMy session is logged out – the account is only yours now.\n"
                             "Thanks for buying! 💚", back)


# ---------------------------------------------------------------- channel / group ownership
async def run_claim(oid: int) -> tuple[str, list]:
    """Verify the buyer joined and move ownership. Returns (text, rows) to show."""
    menu = [B("🏠 Menu", "u:home", D, "back")]
    again = [[B("🔄 Check again", f"u:chk:{oid}", G)], [B("🆔 Use another ID", f"u:other:{oid}", P)]]
    async with lock(f"claim{oid}"):
        o = await db.move_order(oid, ["delivered"], "transferring")
        if not o:
            return "⚠️ This order isn't ready for an ownership transfer.", [menu]
        it = await db.get_stock(o["stock_id"]) or {}
        seller = await db.get_seller(it.get("seller", 0))
        target = o.get("target") or o["user_id"]
        title = clean(it.get("title") or o["title"])
        if not seller:
            return await _manual(o, "seller account missing")
        try:
            await shop_tg.check_and_transfer(seller["session"], seller.get("password"), it["chat_id"], target,
                                             hint=o.get("user_name", ""))
        except shop_tg.NotJoined:
            await db.move_order(oid, ["transferring"], "delivered")
            return (f"❌ I can't see ID `{target}` in **{title}** yet.\nJoin using the link, then tap **Check again**.\n"
                    f"🔗 {o.get('link', '')}"), again
        except Exception as e:
            reason, later = shop_tg.transfer_error(e)
            lg(f"Ownership transfer FAILED order #{oid}: {reason}", "❌")
            if later:
                await db.move_order(oid, ["transferring"], "delivered")
                return f"⏳ {reason}", again
            return await _manual(o, reason)
        await complete_order(o)
        return (f"🎉 **Ownership transferred!**\n**{title}** is now owned by ID `{target}`.\n"
                "Tip: set your own 2-step password and add your admins. Thanks! 💚"), [menu]


async def _manual(o: dict, reason: str) -> tuple[str, list]:
    oid = o["_id"]
    await db.move_order(oid, ["transferring"], "manual", {"manual_reason": reason})
    target = o.get("target") or o["user_id"]
    await notify_owners(f"🛠 **Manual transfer needed – order #{oid}**\n{order_line(o)}\n"
                        f"Give ownership to ID `{target}`.\nReason: {reason}",
                        [[B("🏁 Mark completed", f"so:mc:{oid}", G)]])
    return ("🛠 I couldn't finish the transfer automatically.\nThe owner has been told and will transfer it manually. "
            "You'll get a message here when it is done."), [[B("🏠 Menu", "u:home", D, "back")]]


async def claim_flow(q: CallbackQuery, o: dict):
    if o["status"] != "delivered":
        return await show(q, f"This order is: {STATUS_TXT.get(o['status'], o['status'])}",
                          [[B("🏠 Menu", "u:home", D, "back")]])
    await show(q, "🔎 Verifying that you joined…", [])
    text, rows = await run_claim(o["_id"])
    await show(q, text, rows)


# ================================================================ background: expire unpaid orders
async def reaper():
    try:
        n = await db.sweep_orphans()
        if n:
            lg(f"Released {n} orphan reserved item(s) at startup", "♻️")
    except Exception:
        core.log.exception("sweep failed")
    while True:
        await asyncio.sleep(60)
        try:
            for o in await db.stale_unpaid(db.now()):
                if await cancel_order(o, "expired"):
                    lg(f"Order #{o['_id']} expired – item back in stock", "⌛")
                    try:
                        await send(o["user_id"], f"⌛ Order #{o['_id']} expired because it wasn't paid in time. "
                                                 "The item is back on sale – you can order again.",
                                   [[B("🏠 Menu", "u:home", D, "back")]])
                    except Exception:
                        pass
        except Exception:
            core.log.exception("reaper error")
