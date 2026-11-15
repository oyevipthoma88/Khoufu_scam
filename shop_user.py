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
    n = await db.stock_count({"status": "available"})
    text = (f"👋 **Hey {clean(name)}, welcome to the store!**\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"💰 **Wallet:** {money(u.get('balance', 0))}\n"
            f"📦 **Items in stock:** {n}\n"
            "⚡ **Instant delivery** • 🔐 **Safe & private**\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "✨ Pick a category below to start 👇")
    upd = shop.get("update_link") or ""
    con = shop.get("contact") or ""
    rows = [
        [B("📱 Buy Telegram Account", "u:buy:account", G, "u_acc")],
        [B("📢 Channel", "u:buy:channel:0", G, "u_chan"), B("👥 Group", "u:buy:group:0", G, "u_grp")],
        [B("🤖 Bot", "u:buy:bot:0", G, "u_bot"), B("📚 Method", "u:buy:method:0", G, "u_meth")],
        [B("🧾 My Orders", "u:orders", P, "u_orders"), B("🎟 Redeem", "u:redeem", P, "u_redeem")],
        [ui.U("📢 Updates", _tg_link(upd)) if upd else B("📢 Updates", "u:upd", P, "u_upd"),
         B("📖 How to buy", "u:tut", P, "u_tut"),
         ui.U("👑 Support", _tg_link(con)) if con else B("👑 Support", "u:owner", P, "u_owner")],
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
    if ui.UI.get("start_pic"):
        return await core.send_photo(m.chat.id, ui.UI["start_pic"], t, r)
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
        pic = ui.UI.get("start_pic")
        if pic and q.message.photo:  # already a photo -> just swap caption
            return await show(q, t, r)
        return await show(q, t, r, photo=pic)
    if act == "tut":
        shop = await db.get_shop()
        txt = shop.get("tutorial") or TUTORIAL.format(mins=config.ORDER_TIMEOUT)
        return await show(q, txt, [[B("🔙 Back", "u:home", D, "back")]])
    if act == "owner":
        return await show(q, "👑 Owner contact is not set yet.", [[B("🔙 Back", "u:home", D, "back")]])
    if act == "upd":
        return await show(q, "📢 Update channel is not set yet.", [[B("🔙 Back", "u:home", D, "back")]])
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
    if kind == "account" and len(parts) < 4:
        rows, lines = [], ["📱 **TELEGRAM ACCOUNTS**\n━━━━━━━━━━━━━━━━━━"]
        for t, name in core.TAGS.items():
            n = await db.stock_count({"kind": "account", "status": "available", **core.tag_query(t)})
            lines.append(f"{name} – **{n}** in stock")
            rows.append([B(f"{name} • {n}", f"u:buy:account:{t}", R if t == "scam" else G if t == "normal" else P,
                           core.TAG_KEY[t])])
        lines.append("━━━━━━━━━━━━━━━━━━\n👇 Choose the account type")
        return await show(q, "\n".join(lines), rows + [back])
    if kind == "account":
        tag = parts[3] if parts[3] in core.TAGS else "normal"
        back = [B("🔙 Back", "u:buy:account", D, "back")]
        rows_c = await db.account_countries(core.tag_query(tag))
        if not rows_c:
            return await show(q, f"😔 **{core.TAGS[tag]} accounts are out of stock** right now.\nCheck again later or tap 📢 Update Channel.", [back])
        lines, rows = [f"📱 **{core.TAGS[tag]} account stock**\n"], []
        for c in rows_c:
            cc = c["_id"]
            p = price_for_country(shop, cc, tag)
            label = f"{shop_tg.flag(cc)} {c.get('name') or cc}"
            lines.append(f"{label} – **{c['n']}** in stock – {money(p) if p else 'price not set'}")
            if p:
                rows.append([B(f"{label} • {c['n']} • {money(p)}", f"u:acc:{tag}-{cc}", G)])
        lines.append("━━━━━━━━━━━━━━━━━━\n🌍 Select a country 👇")
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
        tag, cc = core.split_ref(ref)
        aq = {"kind": "account", "country": cc, "status": "available", **core.tag_query(tag)}
        n = await db.stock_count(aq)
        sample = await db.stock_col.find_one(aq)
        if not n or not sample:
            return await show(q, "😔 Sorry, that country just went out of stock.", [[B("🔙 Back", f"u:buy:account:{tag}", D, "back")]])
        p = price_for_country(shop, cc, tag)
        text = (f"📱 **Telegram account** – {core.TAGS[tag]}\n{shop_tg.flag(cc)} Country: **{sample.get('country_name') or cc}**\n"
                f"📦 In stock: **{n}**\n💰 Price: **{money(p)}**\n━━━━━━━━━━━━━━━━━━\n"
                "🎁 You get: number + 2FA password\n📩 Login OTP is sent right here\n🔐 Bot logs out after you log in")
        back_to = f"u:buy:account:{tag}"
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
            tag, cc = core.split_ref(ref)
            query = {"kind": "account", "country": cc, **core.tag_query(tag)}
            price = price_for_country(shop, cc, tag)
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
            "title": (item.get("title") or f"{item.get('country_name', '')} account")
                     + ("" if (item.get("tag") or "normal") == "normal" else f" [{item['tag'].upper()}]"),
            "price": price, "tag": item.get("tag") or "normal", "phone": item.get("phone"),
            "added_by": item.get("added_by"),
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
        pay.append("🖼 Scan the QR code")
    text = (f"💳 **SECURE CHECKOUT**\n━━━━━━━━━━━━━━━━━━\n{KIND_ICON[o['kind']]} **{clean(o['title'])}**\n"
            f"💰 Total: **{money(o['price'])}**   •   👛 Wallet: {money(bal)}\n━━━━━━━━━━━━━━━━━━\n"
            + ("\n".join(pay) + "\n\n" if pay else "") +
            f"**① Pay** exactly **{money(o['price'])}**\n**② Tap** ✅ I have paid\n**③ Send** screenshot / UTR\n━━━━━━━━━━━━━━━━━━\n"
            f"⏳ Auto-cancel in **{config.ORDER_TIMEOUT} min** if unpaid")
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
    rows = [[B(f"{KIND_ICON.get(o['kind'], '')} {clean(o['title'])[:26]} • {money(o['price'])} • {STATUS_TXT.get(o['status'], o['status'])[:14]}", f"u:ord:{o['_id']}", D)]
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
    return await show(q, f"🧾 **Order details**\n{KIND_ICON.get(o['kind'], '')} {clean(o['title'])} – {money(o['price'])}\nStatus: {STATUS_TXT.get(st, st)}", [back])


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
                             f"Amount: **{money(o['price'])}**\n\n/cancel to stop",
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
        proof_file = _proof_media_id(m)
        proof_txt = (m.text or m.caption or "").strip()
        if not proof_file and len(proof_txt) < 4:
            return await m.reply("Please send a **screenshot** (photo or file) or the UTR / transaction ID.")
        o2 = await db.move_order(oid, ["awaiting_payment"], "awaiting_approval",
                                 {"proof_file": proof_file, "proof_text": proof_txt[:200],
                                  "expires": db.now() + dt.timedelta(days=3)})
        SSTATE.pop(uid, None)
        if not o2:
            return await reply(m, "⌛ This order expired. Please order again.", [[B("🏠 Menu", "u:home", D)]])
        lg(f"Payment proof for order #{oid} from {who(m.from_user)}", "🧾")
        await reply(m, f"✅ **Payment submitted** \nThe owner will verify it shortly – "
                       "you'll get a message here as soon as it's approved.", [[B("🏠 Menu", "u:home", D)]])
        await _send_proof_to_owners(oid, o2, m.from_user, proof_file, proof_txt)
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
def _proof_media_id(m: Message) -> str | None:
    """Accept photo, screenshot-as-file, or any image document as proof."""
    if m.photo:
        return m.photo.file_id
    doc = m.document
    if doc:
        mt = (doc.mime_type or "").lower()
        fn = (doc.file_name or "").lower()
        if mt.startswith("image/") or fn.endswith((".png", ".jpg", ".jpeg", ".webp", ".heic")):
            return doc.file_id
    return None


async def _send_proof_to_owners(oid: int, o: dict, buyer, proof_file: str | None, proof_txt: str):
    """Notify every owner + the log channel. A failure to reach one owner never blocks the others."""
    txt = (f"🧾 **New payment – Order #{oid}**\n\n👤 {who(buyer)}\n{order_line(o)}"
           + (f"\n🔖 UTR: `{proof_txt}`" if proof_txt else ""))
    rows = [[B("✅ Approve", f"so:ap:{oid}", G), B("❌ Reject", f"so:rj:{oid}", R)]]
    await notify_owners(txt, rows, photo=proof_file)
    lc = config.LOG_CHANNEL
    if lc:
        try:
            if proof_file:
                await core.send_photo(lc, proof_file, txt, rows)
            else:
                await core.send(lc, txt, rows)
        except Exception as e:
            lg(f"Proof copy to log channel failed: {friendly_err(e)}", "⚠️")


async def route_pending_proof(m: Message) -> bool:
    """A buyer replied with the proof after a restart / lost session.
    Attach it to their most recent order that is still waiting for payment."""
    proof_file = _proof_media_id(m)
    proof_txt = (m.text or m.caption or "").strip()
    if not proof_file and len(proof_txt) < 4:
        return False
    o = await db.orders_col.find_one({"user_id": m.from_user.id, "status": "awaiting_payment"},
                                     sort=[("_id", -1)])
    if not o:
        return False
    o2 = await db.move_order(o["_id"], ["awaiting_payment"], "awaiting_approval",
                             {"proof_file": proof_file, "proof_text": proof_txt[:200],
                              "expires": db.now() + dt.timedelta(days=3)})
    if not o2:
        return False
    lg(f"Payment proof (recovered) for order #{o['_id']} from {who(m.from_user)}", "🧾")
    await reply(m, f"✅ **Payment received for order #{o['_id']}** – the owner will verify it shortly.",
                [[B("🏠 Menu", "u:home", D)]])
    await _send_proof_to_owners(o["_id"], o2, m.from_user, proof_file, proof_txt)
    return True


def _pw(it: dict) -> str:
    return it.get("password") or "none"


async def _sale_approval(o: dict, it: dict):
    """Sale card with Approve / Decline buttons to the owner who added the stock (+ log channel copy)."""
    rows = [[B("✅ Approve", f"so:sok:{o['_id']}", G), B("❌ Decline", f"so:sno:{o['_id']}", R)]]
    await core.notify_adder(it, _sale_card(o, it, "💸 **Your stock was SOLD!**") +
                            "\n\nConfirm this sale so the delivery stays final.", rows)
    lc = config.LOG_CHANNEL
    if lc:
        try:
            await core.send(lc, _sale_card(o, it, f"💸 **SALE – order #{o['_id']}**"), rows)
        except Exception:
            pass


async def sale_confirmed(q: CallbackQuery, oid: int, ok: bool):
    uid = q.from_user.id
    if not core.is_owner(uid):
        return await q.answer("🔒 Owner only", show_alert=True)
    o = await db.get_order(oid)
    if not o:
        return await q.answer("Order not found.", show_alert=True)
    if o.get("sale_ok") or o.get("sale_flag") or o.get("sale_handled"):
        who_name = o.get("sale_handled_by_name") or "an owner"
        res_str = "APPROVED" if o.get("sale_ok") else "DECLINED"
        return await q.answer(f"Already {res_str} by {who_name}!", show_alert=True)

    it = await db.get_stock(o.get("stock_id")) or {}
    name = clean(q.from_user.first_name) or f"Owner {uid}"
    if not ok:
        lg(f"Order #{oid} sale DECLINED by {who(q.from_user)} – releasing stock", "🚫")
        await db.set_order(oid, {
            "sale_flag": True, "sale_flag_by": uid, "sale_handled_by_name": name,
            "sale_flag_at": db.now(), "sale_handled": True, "status": "declined"
        })
        if o.get("stock_id"):
            await db.release_stock(o["stock_id"])
        await q.answer("❌ Sale declined! Stock reclaimed.", show_alert=True)
        txt = (_sale_card(o, it, f"🚫 **SALE DECLINED – order #{oid}**") +
               f"\n\n❌ **Declined by {name}**\nStock has been safely returned to available inventory.")
        await show(q, txt, [])
        try:
            await send(o["user_id"], f"❌ **Notice for order #{oid}:**\nThe sale was declined by the seller. "
                                     "If you made a payment, please contact the owner with your receipt.",
                       [[B("🏠 Menu", "u:home", D)]])
        except Exception:
            pass
        await notify_owners(f"🚩 **Sale #{oid} was DECLINED** by {who(q.from_user)}\n{order_line(o)}\nItem returned to stock.",
                            [[B("🧾 Orders", f"so:ord:{oid}", P)]])
        return

    await db.set_order(oid, {
        "sale_ok": True, "sale_ok_by": uid, "sale_handled_by_name": name,
        "sale_ok_at": db.now(), "sale_handled": True
    })
    if o.get("stock_id"):
        await db.update_stock(o["stock_id"], {"status": "sold", "sold_to": o["user_id"], "sold_at": db.now()})
    lg(f"Order #{oid} sale APPROVED by {who(q.from_user)}", "✅")
    await q.answer("✅ Sale approved!", show_alert=False)
    txt = (_sale_card(o, it, f"💸 **SALE APPROVED – order #{oid}**") +
           f"\n\n✅ **Approved by {name}**\nDelivery confirmed safe – buyer keeps the item.")
    await show(q, txt, [])
    try:
        await send(o["user_id"], f"✅ **Order #{oid} verified and approved by the stock owner!**\nYour account is ready to use safely.",
                   [[B("📩 Get OTP", f"u:otp:{oid}", G)], [B("✅ I'm logged in", f"u:done:{oid}", P)], [B("🏠 Menu", "u:home", D)]])
    except Exception:
        pass


async def delivery_view(o: dict):
    it = await db.get_stock(o["stock_id"]) or {}
    oid = o["_id"]
    if o["kind"] == "account":
        text = (f"✅ **Payment approved – here is your item!**\n\n📱 Number: `+{it.get('phone', '?')}`\n🔐 2FA password: `{_pw(it)}`\n\n"
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
    text = (f"✅ **Payment approved – here is your item!**\n\n{KIND_ICON[o['kind']]} **{clean(it.get('title') or o['title'])}**\n"
            f"🔗 Join here: {link}\n\nJoin the {kind}, then tap **✅ Yes, I joined**.\n"
            f"I will move the ownership to ID `{target}`.\n"
            "Want it on another account? Tap **🆔 Use another ID**.")
    rows = [[B("✅ Yes, I joined", f"u:joined:{oid}", G)], [B("🆔 Use another ID", f"u:other:{oid}", P)]]
    return text, rows


def _sale_card(o: dict, it: dict, head: str) -> str:
    buyer = f"{clean(o.get('user_name'))}" + (f" (@{o['username']})" if o.get("username") else "")
    lines = [head, "", f"🧾 Order: **#{o['_id']}**", f"{KIND_ICON.get(o['kind'], '')} Item: **{clean(o.get('title'))}**"]
    if o["kind"] == "account":
        lines += [f"📱 Number: `+{it.get('phone', '?')}`", f"🆔 Account TG ID: `{it.get('uid', '?')}`",
                  f"🏷 Type: {core.TAGS.get(it.get('tag') or 'normal')}",
                  f"🌍 Country: {shop_tg.flag(it.get('country') or '')} {it.get('country_name') or it.get('country') or '?'}"]
    lines += [f"💰 Price: **{money(o['price'])}** ({o.get('paid_by') or 'UPI/QR'})",
              f"👤 Buyer: {buyer}", f"🆔 Buyer ID: `{o['user_id']}`",
              f"➕ Added by: `{it.get('added_by', '?')}` {clean(it.get('added_by_name'))}",
              f"🕒 Time: {db.now().strftime('%d-%m-%Y %H:%M UTC')}"]
    return "\n".join(lines)


async def complete_order(o: dict, extra: dict | None = None):
    await db.move_order(o["_id"], ["approved", "delivered", "transferring", "logging_out", "manual"],
                        "completed", extra)
    await db.update_stock(o["stock_id"], {"status": "sold", "sold_to": o["user_id"], "sold_at": db.now()},
                          unset={"session": ""} if o["kind"] == "account" else None)
    lg(f"Order #{o['_id']} COMPLETED – {order_line(o)} | buyer {o['user_id']} @{o.get('username') or '-'}", "🏁")
    if o["kind"] == "account":
        it = await db.get_stock(o["stock_id"]) or {}
        lg(f"BOT SESSION LOGGED OUT – #{o['_id']} +{it.get('phone')} (TG id {it.get('uid')}) – waiting for owner "
           f"{it.get('added_by')} to log out own device", "🔐")
        await core.notify_adder(it, _sale_card(o, it, "🔐 **ACTION NEEDED – log out your device!**") +
            "\n\nThe buyer is logged in and the **bot session is logged out**.\n"
            "⚠️ Telegram does not let a new login remove older sessions for **24 hours**, so the buyer "
            "cannot kick you – **you** must log out now:\nTelegram → Settings → Devices → terminate this account "
            "(or log out on your phone).\n\nTap the button after you did it.",
            [[B("✅ I logged out", f"so:olo:{o['_id']}", G)]])


async def deliver(o: dict):
    """Send the purchased item. Safe to call again on 'approved' orders (re-deliver)."""
    oid, uid = o["_id"], o["user_id"]
    try:
        it = await db.get_stock(o["stock_id"])
        if not it:
            raise RuntimeError("stock item missing")
        if o["kind"] == "account":
            await db.move_order(oid, ["approved"], "delivered", {"delivered_at": db.now()})
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
            await send(uid, f"✅ **Payment approved – here is your item!**\n\n🤖 Bot: @{it['bot_username']}\n"
                            f"🔑 Token: `{it['token']}`\n\n⚠️ Open @BotFather → /revoke to get a token only you know.",
                       [[B("🏠 Menu", "u:home", G)]])
            await complete_order(o)
        elif o["kind"] == "method":
            await send(uid, f"✅ **Payment approved – here is your item!**\n\n📚 **{clean(it['title'])}**\n\n{it['content']}",
                       [[B("🏠 Menu", "u:home", G)]])
            await complete_order(o)
        lg(f"SOLD #{oid} | {order_line(o)} | buyer {uid} @{o.get('username') or '-'} | "
           f"stock {it.get('phone') and '+' + it['phone'] or it.get('title')} | added by {it.get('added_by')}", "💸")
        await _sale_approval(o, it)
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
    if o.get("sale_flag") or o.get("status") in ("declined", "cancelled", "rejected"):
        return await q.answer("❌ This sale was declined or cancelled. OTP is disabled.", show_alert=True)
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
        return await show(q, f"🎉 **Done! Purchase completed.**\nMy session is logged out – the account is only yours now.\n"
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
                return f"⌛ {reason}", again
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
            n = await db.sweep_orphans()
            if n:
                lg(f"Released {n} stuck reserved item(s) back to stock", "♻️")
            for o in await db.stale_unpaid(db.now()):
                if await cancel_order(o, "expired"):
                    lg(f"Order #{o['_id']} expired – item back in stock", "⌛")
                    try:
                        await send(o["user_id"], f"⌛ Your order expired because it wasn't paid in time. "
                                                 "The item is back on sale – you can order again.",
                                   [[B("🏠 Menu", "u:home", D, "back")]])
                    except Exception:
                        pass
        except Exception:
            core.log.exception("reaper error")
