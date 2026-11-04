"""Owner side: stock (accounts / channels / groups / bots / methods), prices, QR, codes, approvals."""
import asyncio
import secrets
import time

from bson import ObjectId
from bson.errors import InvalidId
from pyrogram.errors import (FloodWait, PasswordHashInvalid, PhoneCodeExpired, PhoneCodeInvalid,
                             PhoneNumberInvalid, SessionPasswordNeeded)
from pyrogram.types import CallbackQuery, Message

import config
import database as db
import shop_tg
import shop_user
import ui
import userbot
from shop_core import (KIND_ICON, KIND_NAME, SSTATE, STATUS_TXT, B, D, G, P, R, build, clean, clear_state,
                       friendly_err, lg, money, notify_owners, order_line, price_of, reply, send, show, who)
import shop_core as core

ADD_TAG: dict[int, str] = {}  # owner -> tag chosen for the accounts being added
KINDS = ("account", "channel", "group", "bot", "method")
BACK_HOME = [B("🔙 Shop Admin", "so:home", D, "back")]
CANCEL_TXT = "\n\n/cancel to stop"


# ================================================================ screens
async def home_view():
    shop = await db.get_shop()
    counts = {k: await db.stock_count({"kind": k, "status": "available"}) for k in KINDS}
    pend = await db.orders_col.count_documents({"status": {"$in": ["awaiting_approval", "approved", "manual"]}})
    sales = await db.sales_stats()
    text = ("🛍 **SHOP ADMIN**\n━━━━━━━━━━━━━━━━━━\n"
            f"👥 Users: **{await db.count_users()}**\n"
            f"📦 In stock: 📱 {counts['account']} • 📢 {counts['channel']} • 👥 {counts['group']} • "
            f"🤖 {counts['bot']} • 📚 {counts['method']}\n"
            f"🧾 Needs your action: **{pend}**\n"
            f"💵 Sales: **{sales['n']}** orders • **{money(sales['sum'])}**\n━━━━━━━━━━━━━━━━━━\n"
            f"🖼 QR: {'✅' if shop.get('qr') else '❌'}  🏦 UPI: {('`' + shop['upi'] + '`') if shop.get('upi') else '❌'}\n"
            f"🔒 Must-join: {len(shop['force']) + len(config.FORCE_JOIN)}  "
            f"📣 Update link: {'✅' if shop.get('update_link') else '❌'}  👑 Contact: {'✅' if shop.get('contact') else '❌'}")
    rows = [
        [B("➕ Add Stock", "so:add", G), B("📦 Stock List", "so:stock", P)],
        [B("🧾 Orders", "so:pend", G), B("💰 Set Price", "so:price", P)],
        [B("🖼 Set QR", "so:qr", P), B("🏦 Set UPI", "so:upi", P)],
        [B("🎟 Redeem Codes", "so:codes", P), B("🔒 Must-Join", "so:force", P)],
        [B("📣 Update Link", "so:set:update_link", D), B("👑 Contact", "so:set:contact", D)],
        [B("📖 Set Tutorial", "so:set:tutorial", D), B("👤 Seller Accounts", "so:sellers", D)],
        [B("🛒 Open User View", "u:home", D), B("🔙 Owner menu", "home", D, "back")],
    ]
    return text, rows


# ================================================================ callbacks
async def admin_cb(q: CallbackQuery):
    d, uid = q.data, q.from_user.id
    try:
        await q.answer()
    except Exception:
        pass
    if not core.is_owner(uid):
        return await q.answer("🔒 Owner only", show_alert=True)
    p = d.split(":")
    act = p[1]

    if act == "home":
        await clear_state(uid)
        t, r = await home_view()
        return await show(q, t, r)

    # ---------- add stock
    if act == "add":
        return await show(q, "➕ **Add stock** – what do you want to sell?", [
            [B("📱 Add Account", "so:addk:account", G), B("📢 Add Channel", "so:addk:channel", G)],
            [B("👥 Add GC", "so:addk:group", G), B("🤖 Add Bot", "so:addk:bot", G)],
            [B("📚 Add Method", "so:addk:method", G)], BACK_HOME])
    if act == "addk":
        kind = p[2]
        if kind == "account":
            return await show(q, "📱 **Add account(s)** – which type of account?\n\n"
                                 "✅ **Normal** – clean account\n⚠️ **Scam Tag** – account has SCAM label\n"
                                 "🎭 **Fake Tag** – account has FAKE label", [
                [B("✅ Add Normal Account", "so:addt:normal", G, "t_normal")],
                [B("⚠️ Add Scam Tag", "so:addt:scam", R, "t_scam"), B("🎭 Add Fake Tag", "so:addt:fake", P, "t_fake")],
                [B("🔙 Back", "so:add", D, "back")]])
        if kind in ("channel", "group"):
            sellers = await db.all_sellers()
            rows = [[B(f"👤 {s['info'].get('name', s['_id'])[:25]} ({s['_id']})", f"so:sel:{kind}:{s['_id']}", P)]
                    for s in sellers]
            rows.append([B("➕ New owner account (String / Phone)", f"so:seln:{kind}", G)])
            rows.append([B("🔙 Back", "so:add", D, "back")])
            return await show(q, f"{KIND_ICON[kind]} **Add {KIND_NAME[kind]}**\n\nChoose the account that **owns** the "
                                 f"{kind}s you want to sell, or log in a new one.", rows)
        if kind == "bot":
            SSTATE[uid] = {"step": "bot_token"}
            return await show(q, "🤖 Send the **bot token** (from @BotFather). I verify it automatically." + CANCEL_TXT,
                              [[B("🔙 Back", "so:add", D, "back")]])
        if kind == "method":
            SSTATE[uid] = {"step": "m_title"}
            return await show(q, "📚 Send the **title** of the method." + CANCEL_TXT, [[B("🔙 Back", "so:add", D, "back")]])
    if act == "addt":
        tag = p[2] if p[2] in core.TAGS else "normal"
        ADD_TAG[uid] = tag
        SSTATE[uid] = {"step": "lm", "purpose": "account"}
        return await show(q, f"📱 **Add {core.TAGS[tag]} account(s)** – how do you want to log in?",
                          _lm_rows("so:addk:account"))
    if act == "seln":
        SSTATE[uid] = {"step": "lm", "purpose": p[2]}
        return await show(q, "👤 **New owner account** – how do you want to log in?\n\n"
                             "ℹ️ Telegram only allows ownership transfer ~24h after a session is created and needs the "
                             "account's **2-step password**.", _lm_rows(f"so:addk:{p[2]}"))
    if act == "sel":
        kind, sid = p[2], int(p[3])
        SSTATE[uid] = {"step": "urls", "kind": kind, "seller": sid}
        return await show(q, f"🔗 Send the **{kind} URLs** this account owns – one or many (one per line).\n"
                             "Example: `https://t.me/mychannel` or private invite links." + CANCEL_TXT,
                          [[B("🔙 Back", f"so:addk:{kind}", D, "back")]])
    if act == "lm":  # login method chosen
        st = SSTATE.get(uid)
        if not st or st.get("step") != "lm":
            return
        if p[2] == "s":
            st.update(step="l2fa")
            return await show(q, "🔐 Send the account's **2-step password** (needed to hand over ownership / give to buyer).\n"
                                 "Send `-` if the account has none." + CANCEL_TXT, [])
        st.update(step="lphone")
        return await show(q, "📱 Send the **phone number** with country code, e.g. `+919876543210`." + CANCEL_TXT, [])

    # ---------- stock list
    if act == "stock":
        rows, lines = [], ["📦 **Stock**\n"]
        for k in KINDS:
            n = await db.stock_count({"kind": k, "status": "available"})
            r = await db.stock_count({"kind": k, "status": "reserved"})
            s = await db.stock_count({"kind": k, "status": "sold"})
            lines.append(f"{KIND_ICON[k]} {KIND_NAME[k]}: **{n}** available • {r} reserved • {s} sold")
            rows.append([B(f"{KIND_ICON[k]} {KIND_NAME[k]} list", f"so:list:{k}:0", P)])
        return await show(q, "\n".join(lines), rows + [BACK_HOME])
    if act == "list":
        return await stock_list(q, p[2], int(p[3]))
    if act == "del":
        return await show(q, "🗑 Delete this item from stock?", [[B("✅ Yes, delete", f"so:delc:{p[2]}", R),
                                                                B("❌ No", "so:stock", D)]])
    if act == "delc":
        try:
            ok = await db.delete_stock(ObjectId(p[2]))
        except InvalidId:
            ok = False
        lg(f"Stock item {p[2]} deleted: {ok}", "🗑")
        return await show(q, "🗑 Deleted." if ok else "⚠️ Can't delete – it is reserved or already sold.",
                          [[B("🔙 Stock", "so:stock", D, "back")]])

    # ---------- prices
    if act == "price":
        shop = await db.get_shop()
        pr = shop["prices"]
        lines = ["💰 **Prices**\n"]
        for k in KINDS:
            v = pr.get(k)
            if k == "account":
                v = v if isinstance(v, dict) else {}
                lines.append("📱 **Accounts**")
                for t, tname in core.TAGS.items():
                    pre = core.acc_key(t, "")
                    own = {kk[len(pre):]: x for kk, x in v.items()
                           if (kk.startswith(pre) if pre else "-" not in kk)}
                    d = own.pop("_", None)
                    extra = ", ".join(f"{shop_tg.flag(c)} {c} {money(x)}" for c, x in own.items())
                    lines.append(f"   {tname}: default {money(d) if d else '—'}" + (f" • {extra}" if extra else ""))
            else:
                lines.append(f"{KIND_ICON[k]} {KIND_NAME[k]}: {money(v) if v else '— (per item)'}")
        rows = [[B(f"{KIND_ICON[k]} {KIND_NAME[k]}", f"so:pk:{k}", P)] for k in KINDS]
        return await show(q, "\n".join(lines) + "\n\nWhat do you want to price?", rows + [BACK_HOME])
    if act == "pk":
        kind = p[2]
        if kind == "account":
            rows = [[B(name, f"so:pt:{t}", R if t == "scam" else G if t == "normal" else P, core.TAG_KEY[t])]
                    for t, name in core.TAGS.items()]
            return await show(q, "📱 **Account price** – which account type?\n"
                                 "Each type has its own prices, even for the same country.",
                              rows + [[B("🔙 Back", "so:price", D, "back")]])
        d = f"so:pc:{kind}:_"
        q.data = d
        return await admin_cb(q)
    if act == "pt":
        tag = core.norm_tag(p[2])
        cs = await db.account_countries(core.tag_query(tag))
        shop = await db.get_shop()
        dflt = core.price_for_country(shop, "__none__", tag)
        rows = [[B(f"🌍 Default – all countries ({money(dflt) if dflt else '—'})",
                   f"so:pc:account:{core.acc_key(tag, '_')}", G)]]
        for c in cs:
            pr = core.price_for_country(shop, c["_id"], tag)
            rows.append([B(f"{shop_tg.flag(c['_id'])} {c.get('name') or c['_id']} ({c['n']}) • {money(pr) if pr else '—'}",
                           f"so:pc:account:{core.acc_key(tag, c['_id'])}", P)])
        return await show(q, f"📱 **{core.TAGS[tag]}** – price for which country?\n"
                             "A country price beats the default of this type.",
                          rows + [[B("🔙 Back", "so:pk:account", D, "back")]])
    if act == "pc":
        kind, cc = p[2], p[3]
        SSTATE[uid] = {"step": "price", "kind": kind, "cc": cc}
        if kind == "account":
            t, c = core.split_ref(cc)
            label = f"{KIND_NAME[kind]} {core.TAGS[t]} – " + ("all countries" if c == "_" else f"{shop_tg.flag(c)} {c}")
        else:
            label = KIND_NAME[kind]
        return await show(q, f"💰 Send the **price** for **{label}** (number only, e.g. `99`).\n"
                             "Send `0` to remove it." + CANCEL_TXT, [[B("🔙 Back", "so:price", D, "back")]])

    # ---------- QR / UPI / texts
    if act == "qr":
        SSTATE[uid] = {"step": "qr"}
        return await show(q, "🖼 Send the **payment QR** as a photo." + CANCEL_TXT, [BACK_HOME])
    if act == "upi":
        SSTATE[uid] = {"step": "upi"}
        return await show(q, "🏦 Send your **UPI ID** (or `-` to remove)." + CANCEL_TXT, [BACK_HOME])
    if act == "set":
        field = p[2]
        SSTATE[uid] = {"step": "set", "field": field}
        hint = {"update_link": "Send the **update channel** link / @username (or `-` to remove).",
                "contact": "Send the **owner contact**: @username or numeric ID (or `-` to remove).",
                "tutorial": "Send the full **tutorial text** shown to users (or `-` for the default)."}[field]
        return await show(q, hint + CANCEL_TXT, [BACK_HOME])

    # ---------- redeem codes
    if act == "codes":
        codes = await db.list_codes()
        lines = ["🎟 **Redeem codes** (credit added to the user's balance)\n"]
        rows = []
        for c in codes:
            lines.append(f"`{c['_id']}` – {money(c['amount'])} – left {c['left']}/{c['uses']}")
            rows.append([B(f"🗑 {c['_id']}", f"so:cd:{c['_id']}", R)])
        if not codes:
            lines.append("No codes yet.")
        return await show(q, "\n".join(lines), [[B("➕ Create code", "so:cc", G)]] + rows[:12] + [BACK_HOME])
    if act == "cc":
        SSTATE[uid] = {"step": "code_amount"}
        return await show(q, "🎟 Send the **amount** this code gives (e.g. `100`)." + CANCEL_TXT, [[B("🔙 Back", "so:codes", D)]])
    if act == "cd":
        await db.delete_code(p[2])
        q.data = "so:codes"
        return await admin_cb(q)

    # ---------- must-join
    if act == "force":
        shop = await db.get_shop()
        lines = ["🔒 **Must-join channels**\nUsers must join all of these before they can use the bot.\n"
                 "⚠️ The bot must be **admin** in each one.\n"]
        rows = []
        for i, ch in enumerate(shop["force"]):
            lines.append(f"{i + 1}. {clean(ch['title'])} (`{ch['chat']}`)")
            rows.append([B(f"🗑 {ch['title'][:30]}", f"so:fd:{i}", R)])
        for ref in config.FORCE_JOIN:
            lines.append(f"• {ref} (from FORCE_JOIN env)")
        if not shop["force"] and not config.FORCE_JOIN:
            lines.append("None set – everyone can use the bot.")
        return await show(q, "\n".join(lines), [[B("➕ Add channel", "so:fa", G)]] + rows + [BACK_HOME])
    if act == "fa":
        SSTATE[uid] = {"step": "force_add"}
        return await show(q, "🔒 Send the channel **@username**, link or `-100…` ID. The bot must be admin there." + CANCEL_TXT,
                          [[B("🔙 Back", "so:force", D)]])
    if act == "fd":
        shop = await db.get_shop()
        i = int(p[2])
        if 0 <= i < len(shop["force"]):
            shop["force"].pop(i)
            await db.set_shop("force", shop["force"])
            shop_user._ok_until.clear()
        q.data = "so:force"
        return await admin_cb(q)

    # ---------- sellers
    if act == "sellers":
        sellers = await db.all_sellers()
        rows = [[B(f"🗑 {s['info'].get('name', s['_id'])[:25]} ({s['_id']})", f"so:seld:{s['_id']}", R)] for s in sellers]
        return await show(q, "👤 **Seller accounts** (they own the channels / groups you sell)\n" +
                          ("" if sellers else "None yet – added when you use Add Channel / Add GC."), rows + [BACK_HOME])
    if act == "seld":
        n = await db.stock_count({"seller": int(p[2]), "status": {"$in": ["available", "reserved"]}})
        if n:
            return await show(q, f"⚠️ This account still has **{n}** item(s) in stock. Delete those first.",
                              [[B("🔙 Back", "so:sellers", D)]])
        await db.remove_seller(int(p[2]))
        q.data = "so:sellers"
        return await admin_cb(q)

    # ---------- orders
    if act == "pend":
        orders = await db.orders_by_status(["awaiting_approval", "approved", "manual"])
        if not orders:
            return await show(q, "🧾 Nothing needs your action. 🎉", [BACK_HOME])
        rows = [[B(f"{order_line(o)[:34]} • {STATUS_TXT[o['status']][:12]}", f"so:ord:{o['_id']}", P)] for o in orders]
        return await show(q, "🧾 **Orders needing action**", rows + [BACK_HOME])
    if act == "ord":
        return await order_detail(q, int(p[2]))
    if act in ("ap", "rj", "rd", "mc", "olo"):
        return await order_action(q, act, int(p[2]))


def _lm_rows(back: str):
    return [[B("🔑 String Session", "so:lm:s", G), B("📱 Phone Login", "so:lm:p", G)], [B("🔙 Back", back, D, "back")]]


async def stock_list(q: CallbackQuery, kind: str, page: int):
    items = await db.stock_find({"kind": kind, "status": {"$in": ["available", "reserved"]}}, limit=11, skip=page * 10)
    nxt = len(items) > 10
    items = items[:10]
    shop = await db.get_shop()
    lines = [f"{KIND_ICON[kind]} **{KIND_NAME[kind]} stock**\n"]
    rows = []
    for it in items:
        tag = "🔒" if it["status"] == "reserved" else "🟢"
        if kind == "account":
            name = f"+{it['phone']} {it.get('country', '')} {core.TAGS.get(it.get('tag') or 'normal', '')[:2]}"
        elif kind == "bot":
            name = f"@{it['bot_username']}"
        else:
            name = clean(it["title"])
        pr = price_of(shop, it)
        lines.append(f"{tag} {name} – {money(pr) if pr else 'no price'}")
        if it["status"] == "available":
            rows.append([B(f"🗑 {name[:30]}", f"so:del:{it['_id']}", R)])
    if not items:
        lines.append("Empty.")
    nav = []
    if page:
        nav.append(B("⬅️", f"so:list:{kind}:{page - 1}", D))
    if nxt:
        nav.append(B("➡️", f"so:list:{kind}:{page + 1}", D))
    if nav:
        rows.append(nav)
    return await show(q, "\n".join(lines), rows + [[B("🔙 Stock", "so:stock", D, "back")]])


# ---------------------------------------------------------------- orders
async def order_detail(q: CallbackQuery, oid: int):
    o = await db.get_order(oid)
    if not o:
        return await show(q, "Order not found.", [BACK_HOME])
    text = (f"🧾 **Order #{oid}** – {STATUS_TXT.get(o['status'], o['status'])}\n\n"
            f"👤 {clean(o.get('user_name'))} (@{o.get('username') or '-'}) [`{o['user_id']}`]\n{order_line(o)}"
            + (f"\n🔖 UTR: `{o['proof_text']}`" if o.get("proof_text") else "")
            + (f"\n🛠 {o['manual_reason']}" if o.get("manual_reason") else ""))
    rows = []
    if o["status"] == "awaiting_approval":
        rows.append([B("✅ Approve", f"so:ap:{oid}", G), B("❌ Reject", f"so:rj:{oid}", R)])
    if o["status"] in ("approved", "delivered"):
        rows.append([B("📦 Deliver again", f"so:rd:{oid}", G)])
    if o["status"] == "manual":
        rows.append([B("🏁 Mark completed", f"so:mc:{oid}", G)])
    rows.append([B("🔙 Orders", "so:pend", D, "back")])
    await show(q, text, rows, photo=o.get("proof_file") if o["status"] == "awaiting_approval" else None)


async def order_action(q: CallbackQuery, act: str, oid: int):
    uid = q.from_user.id
    back = [[B("🧾 Orders", "so:pend", D, "back")]]
    if act == "ap":
        o = await db.move_order(oid, ["awaiting_approval"], "approved", {"approved_by": uid})
        if not o:
            return await q.answer("Already handled (or cancelled).", show_alert=True)
        lg(f"Order #{oid} APPROVED by {who(q.from_user)}", "✅")
        await show(q, f"✅ Order #{oid} approved by {clean(q.from_user.first_name)} – delivering…", back)
        return await shop_user.deliver(o)
    if act == "rj":
        o = await db.move_order(oid, ["awaiting_approval"], "rejected", {"rejected_by": uid})
        if not o:
            return await q.answer("Already handled (or cancelled).", show_alert=True)
        await db.release_stock(o["stock_id"])
        lg(f"Order #{oid} REJECTED by {who(q.from_user)}", "❌")
        await show(q, f"❌ Order #{oid} rejected.", back)
        try:
            await send(o["user_id"], f"❌ **Order #{oid} was rejected.**\nYour payment couldn't be verified. "
                                     "If you did pay, contact the owner with your screenshot.",
                       [[B("🏠 Menu", "u:home", D, "back")]])
        except Exception:
            pass
        return
    o = await db.get_order(oid)
    if not o:
        return await show(q, "Order not found.", back)
    if act == "olo":
        await db.set_order(oid, {"owner_logged_out": True, "owner_logout_at": db.now()})
        lg(f"Order #{oid}: owner {who(q.from_user)} confirmed own device LOGGED OUT", "🔐")
        return await show(q, f"🔐 Thanks! Order #{oid} – your device logout is recorded.", back)
    if act == "rd":
        if o["status"] not in ("approved", "delivered"):
            return await q.answer("Not in a deliverable state.", show_alert=True)
        await show(q, f"📦 Re-delivering order #{oid}…", back)
        return await shop_user.deliver(o)
    if act == "mc":
        if o["status"] != "manual":
            return await q.answer("Not waiting for a manual finish.", show_alert=True)
        await shop_user.complete_order(o)
        await show(q, f"🏁 Order #{oid} marked completed.", back)
        try:
            await send(o["user_id"], f"🎉 **Order #{oid} is complete!** Ownership has been transferred to you. Thanks! 💚",
                       [[B("🏠 Menu", "u:home", D, "back")]])
        except Exception:
            pass


# ================================================================ text / photo input
async def admin_input(m: Message, st: dict):
    uid = m.from_user.id
    step = st["step"]
    text = (m.text or "").strip()

    # ---------- plain settings
    if step == "price":
        try:
            val = float(text.replace(",", ""))
            if val < 0:
                raise ValueError
        except ValueError:
            return await m.reply("❌ Send a number like `99` (or `0` to remove)." + CANCEL_TXT)
        shop = await db.get_shop()
        kind, cc = st["kind"], st["cc"]
        if kind == "account":
            cur = shop["prices"].get("account") if isinstance(shop["prices"].get("account"), dict) else {}
            if val:
                cur[cc] = val
            else:
                cur.pop(cc, None)
            await db.set_shop("prices.account", cur)
        else:
            await db.set_shop(f"prices.{kind}", val)
        SSTATE.pop(uid, None)
        lg(f"Price set: {kind} {cc} | new price {money(val) if val else 'removed'} | by {who(m.from_user)}", "💰")
        return await reply(m, f"✅ Price saved: **{money(val) if val else 'removed'}**", [[B("💰 Prices", "so:price", G), *BACK_HOME]])

    if step == "qr":
        if not m.photo:
            return await m.reply("Please send the QR as a **photo**." + CANCEL_TXT)
        await db.set_shop("qr", m.photo.file_id)
        SSTATE.pop(uid, None)
        lg("Payment QR updated", "🖼")
        return await reply(m, "✅ QR saved. Buyers will see it at checkout.", [BACK_HOME])

    if step == "upi":
        await db.set_shop("upi", "" if text == "-" else text[:80])
        SSTATE.pop(uid, None)
        return await reply(m, "✅ UPI saved." if text != "-" else "✅ UPI removed.", [BACK_HOME])

    if step == "set":
        field = st["field"]
        if not text:
            return await m.reply("Please send text." + CANCEL_TXT)
        val = "" if text == "-" else text[:3500]
        if field == "contact" and val:
            val = val if val.startswith("http") else val.lstrip("@")
        await db.set_shop(field, val)
        SSTATE.pop(uid, None)
        lg(f"Setting '{field}' updated", "⚙️")
        return await reply(m, "✅ Saved.", [BACK_HOME])

    if step == "code_amount":
        try:
            amt = float(text.replace(",", ""))
            if amt <= 0:
                raise ValueError
        except ValueError:
            return await m.reply("❌ Send a positive number." + CANCEL_TXT)
        st.update(step="code_uses", amount=amt)
        return await m.reply("👥 How many **users** can use this code? (e.g. `1` or `50`)" + CANCEL_TXT)

    if step == "code_uses":
        if not text.isdigit() or not (1 <= int(text) <= 100000):
            return await m.reply("❌ Send a whole number (1 or more)." + CANCEL_TXT)
        st.update(step="code_name", uses=int(text))
        return await m.reply("✏️ Send the **code text** you want (letters/numbers), or `-` for a random one." + CANCEL_TXT)

    if step == "code_name":
        code = secrets.token_hex(4).upper() if text == "-" else text.upper().replace(" ", "")
        if not (4 <= len(code) <= 30) or not code.isalnum():
            return await m.reply("❌ 4–30 letters/numbers only." + CANCEL_TXT)
        if await db.get_code(code):
            return await m.reply("❌ That code already exists. Send another." + CANCEL_TXT)
        await db.create_code(code, st["amount"], st["uses"], uid)
        SSTATE.pop(uid, None)
        lg(f"Redeem code {code} created: {money(st['amount'])} x{st['uses']}", "🎟")
        return await reply(m, f"✅ **Code created**\n\n`{code}`\nValue: **{money(st['amount'])}** • Uses: **{st['uses']}**",
                           [[B("🎟 Codes", "so:codes", G), *BACK_HOME]])

    if step == "force_add":
        ref = text.replace("https://t.me/", "").replace("t.me/", "").lstrip("@")
        ref = int(ref) if ref.lstrip("-").isdigit() else ref
        try:
            chat = await core.BOT.get_chat(ref)
            me = await core.BOT.get_chat_member(chat.id, "me")
            if me.status.name not in ("ADMINISTRATOR", "OWNER"):
                raise ValueError("bot is not admin there")
            link = f"https://t.me/{chat.username}" if chat.username else (
                chat.invite_link or await core.BOT.export_chat_invite_link(chat.id))
        except Exception as e:
            return await m.reply(f"❌ Can't use that channel: {friendly_err(e)}\nAdd the bot as **admin** there and send again."
                                 + CANCEL_TXT)
        shop = await db.get_shop()
        shop["force"] = [f for f in shop["force"] if f["chat"] != chat.id] + [
            {"chat": chat.id, "title": chat.title or str(chat.id), "link": link}]
        await db.set_shop("force", shop["force"])
        shop_user._ok_until.clear()
        SSTATE.pop(uid, None)
        lg(f"Must-join added: {chat.title} ({chat.id})", "🔒")
        return await reply(m, f"✅ **{clean(chat.title)}** is now a must-join channel.", [[B("🔒 Must-join", "so:force", G), *BACK_HOME]])

    # ---------- bot / method stock
    if step == "bot_token":
        try:
            info = await shop_tg.check_bot_token(text)
        except ValueError as e:
            return await m.reply(f"❌ {e}. Send the token again." + CANCEL_TXT)
        try:
            await m.delete()
        except Exception:
            pass
        st.update(step="bot_price", token=text, bot_username=info.get("username", "?"), name=info.get("first_name", ""))
        return await m.reply(f"✅ Bot verified: @{info.get('username')}\n💰 Send its **price** (or `-` to use the Bot default price)." + CANCEL_TXT)

    if step == "bot_price":
        price = _price_or_default(text)
        if price is None:
            return await m.reply("❌ Send a number or `-`." + CANCEL_TXT)
        await db.add_stock({"added_by": uid, "kind": "bot", "token": st["token"], "bot_username": st["bot_username"],
                            "title": f"@{st['bot_username']}", "price": price})
        SSTATE.pop(uid, None)
        lg(f"Bot added to stock: @{st['bot_username']}", "🤖")
        return await reply(m, f"✅ **@{st['bot_username']}** added to stock.", [[B("➕ Add more", "so:addk:bot", G), *BACK_HOME]])

    if step == "m_title":
        if not text:
            return await m.reply("Send the title as text." + CANCEL_TXT)
        st.update(step="m_desc", title=text[:80])
        return await m.reply("📝 Send a short **description** buyers see before paying (or `-`)." + CANCEL_TXT)
    if step == "m_desc":
        st.update(step="m_content", desc="" if text == "-" else text[:300])
        return await m.reply("🔐 Now send the **secret content** the buyer gets after payment (text/links)." + CANCEL_TXT)
    if step == "m_content":
        if not text:
            return await m.reply("Send the content as text." + CANCEL_TXT)
        st.update(step="m_price", content=text[:3500])
        return await m.reply("💰 Send the **price** (or `-` for the Method default price)." + CANCEL_TXT)
    if step == "m_price":
        price = _price_or_default(text)
        if price is None:
            return await m.reply("❌ Send a number or `-`." + CANCEL_TXT)
        await db.add_stock({"added_by": uid, "kind": "method", "title": st["title"], "desc": st["desc"], "content": st["content"],
                            "price": price})
        SSTATE.pop(uid, None)
        lg(f"Method added to stock: {st['title']}", "📚")
        return await reply(m, f"✅ **{clean(st['title'])}** added to stock.", [[B("➕ Add more", "so:addk:method", G), *BACK_HOME]])

    # ---------- login flows
    if step == "l2fa":
        if not text:
            return await m.reply("Send the password as text (or `-`)." + CANCEL_TXT)
        try:
            await m.delete()
        except Exception:
            pass
        st.update(step="lstring", pw=None if text == "-" else text)
        return await m.reply("🔑 Now send the **string session**" + (" (one or many, one per line)." if st["purpose"] == "account" else ".") + CANCEL_TXT)

    if step == "lstring":
        try:
            await m.delete()
        except Exception:
            pass
        lines = [x.strip() for x in text.splitlines() if x.strip()]
        if not lines:
            return await m.reply("Send the session text." + CANCEL_TXT)
        if st["purpose"] != "account":
            lines = lines[:1]
        pw = st.get("pw")
        SSTATE.pop(uid, None)
        wait = await m.reply(f"⏳ Checking {len(lines)} session(s)…")
        docs, errs = [], []
        for i, line in enumerate(lines, 1):
            try:
                docs.append(await asyncio.wait_for(shop_tg.validate_session(line), 90))
            except Exception as e:
                errs.append(f"#{i}: {friendly_err(e)}")
            await asyncio.sleep(1)
        try:
            await wait.delete()
        except Exception:
            pass
        return await after_login(m, st["purpose"], docs, pw, errs)

    if step == "lphone":
        phone = "".join(ch for ch in text if ch.isdigit() or ch == "+")
        if len(phone.lstrip("+")) < 7:
            return await m.reply("❌ That doesn't look like a phone number. Example: `+919876543210`")
        c = userbot.new_client(None, "shoplogin")
        try:
            await c.connect()
            sent = await c.send_code(phone)
        except PhoneNumberInvalid:
            await _disc(c)
            return await m.reply("❌ Invalid phone number. Try again or /cancel.")
        except Exception as e:
            await _disc(c)
            SSTATE.pop(uid, None)
            return await reply(m, f"❌ {friendly_err(e)}", [BACK_HOME])
        st.update(step="lcode", client=c, phone=phone, hash=sent.phone_code_hash, t=time.time())
        return await m.reply("📩 OTP sent. Send it **with spaces**, e.g. `1 2 3 4 5`" + CANCEL_TXT)

    if step == "lcode":
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
            st["step"] = "lpw"
            return await m.reply("🔐 2-step verification is on. Send the **password**." + CANCEL_TXT)
        except PhoneCodeInvalid:
            return await m.reply("❌ Wrong code. Send it again or /cancel.")
        except PhoneCodeExpired:
            await clear_state(uid)
            return await reply(m, "❌ Code expired. Start again.", [BACK_HOME])
        except Exception as e:
            await clear_state(uid)
            return await reply(m, f"❌ Login failed: {friendly_err(e)}", [BACK_HOME])
        return await _phone_done(m, uid, st, None)

    if step == "lpw":
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
            await clear_state(uid)
            return await reply(m, f"❌ Login failed: {friendly_err(e)}", [BACK_HOME])
        return await _phone_done(m, uid, st, text)

    if step == "urls":
        return await add_chats(m, st, text)

    if step == "lm":
        return await m.reply("Please use the buttons above, or /cancel.")


def _price_or_default(text: str):
    if text == "-":
        return 0
    try:
        v = float(text.replace(",", ""))
        return v if v >= 0 else None
    except ValueError:
        return None


async def _disc(c):
    try:
        await c.disconnect()
    except Exception:
        pass


async def _phone_done(m: Message, uid: int, st: dict, pw: str | None):
    c = st["client"]
    purpose = st["purpose"]
    SSTATE.pop(uid, None)
    try:
        me = await c.get_me()
        session = await c.export_session_string()
    finally:
        await _disc(c)
    await after_login(m, purpose, [shop_tg.account_doc(me, session)], pw, [])


async def after_login(m: Message, purpose: str, docs: list[dict], pw: str | None, errs: list[str]):
    uid = m.from_user.id
    err_txt = ("\n\n⚠️ **Errors:**\n" + "\n".join(errs)) if errs else ""
    if not docs:
        return await reply(m, "❌ No valid session." + err_txt, [BACK_HOME])
    if purpose == "account":
        added, dup = [], []
        tag = ADD_TAG.pop(uid, "normal")
        for d in docs:
            if await db.stock_col.find_one({"kind": "account", "uid": d["uid"], "status": {"$in": ["available", "reserved"]}}):
                dup.append(d["phone"])
                continue
            await db.add_stock({"kind": "account", "uid": d["uid"], "session": d["session"], "phone": d["phone"],
                                "country": d["country"], "country_name": d["country_name"], "password": pw,
                                "title": f"{d['country_name']} account", "tg_name": d["name"], "tag": tag,
                                "added_by": uid, "added_by_name": m.from_user.first_name or ""})
            added.append(f"{shop_tg.flag(d['country'])} +{d['phone']} (TG id {d['uid']})")
        lg(f"STOCK ADDED by {who(m.from_user)} | type: {core.TAGS[tag]} | {len(added)} account(s): "
           f"{', '.join(added)[:500]}" + (f" | already in stock: {', '.join(dup)}" if dup else ""), "📱")
        shop = await db.get_shop()
        warn = ""
        if added and not any(price_of(shop, {"kind": "account", "country": d["country"]}) for d in docs):
            warn = "\n\n⚠️ No account price set yet – use 💰 Set Price or buyers can't purchase."
        text = (f"✅ **{len(added)} {core.TAGS[tag]} account(s) added to stock**\n" + "\n".join(added) +
                (f"\n\n♻️ Already in stock: {', '.join(dup)}" if dup else "") + err_txt + warn)
        return await reply(m, text, [[B("➕ Add more", "so:addk:account", G), B("💰 Prices", "so:price", P)], BACK_HOME])
    d = docs[0]
    await db.save_seller(d["uid"], d["session"], {"name": d["name"], "phone": d["phone"]}, pw)
    SSTATE[uid] = {"step": "urls", "kind": purpose, "seller": d["uid"]}
    lg(f"Seller account saved: {d['name']} ({d['uid']})", "👤")
    note = "" if pw else "\n⚠️ No 2-step password saved – automatic ownership transfer will NOT work for this account."
    return await reply(m, f"✅ Owner account **{clean(d['name'])}** saved.{note}\n\n🔗 Now send the **{purpose} URLs** it owns "
                          "– one or many (one per line)." + CANCEL_TXT, [])


async def add_chats(m: Message, st: dict, text: str):
    uid = m.from_user.id
    kind, sid = st["kind"], st["seller"]
    refs = [x.strip() for x in text.replace(",", "\n").splitlines() if x.strip()]
    if not refs:
        return await m.reply("Send at least one URL." + CANCEL_TXT)
    seller = await db.get_seller(sid)
    if not seller:
        SSTATE.pop(uid, None)
        return await reply(m, "❌ That owner account no longer exists.", [BACK_HOME])
    SSTATE.pop(uid, None)
    status = await m.reply(f"⏳ Checking {len(refs)} link(s)…")
    ok, bad = [], []
    try:
        async with userbot.account_client(seller["session"]) as c:
            for i, raw_ref in enumerate(refs[:50], 1):
                ref = shop_tg.parse_chat_ref(raw_ref)
                if ref is None:
                    bad.append(f"{raw_ref[:40]} – not a valid link")
                    continue
                for attempt in range(2):
                    try:
                        info = await shop_tg.inspect_chat(c, ref, kind)
                        if await db.stock_col.find_one({"chat_id": info["chat_id"], "status": {"$in": ["available", "reserved"]}}):
                            bad.append(f"{info['title']} – already in stock")
                            break
                        await db.add_stock({"kind": kind, "seller": sid, "added_by": m.from_user.id, **info})
                        ok.append(info["title"])
                        break
                    except FloodWait as e:
                        if attempt or e.value > 90:
                            bad.append(f"{raw_ref[:40]} – Telegram flood wait {e.value}s, send it again later")
                            break
                        await asyncio.sleep(e.value + 1)
                    except Exception as e:
                        bad.append(f"{raw_ref[:40]} – {friendly_err(e)}")
                        break
                if i % 5 == 0:
                    try:
                        await status.edit_text(f"⏳ Checked {i}/{len(refs)}…")
                    except Exception:
                        pass
                await asyncio.sleep(1.2)
    except Exception as e:
        lg(f"Add {kind} failed: {friendly_err(e)}", "❌")
        return await reply(m, f"❌ Seller account problem: {friendly_err(e)}", [BACK_HOME])
    try:
        await status.delete()
    except Exception:
        pass
    lg(f"{kind}s added: {len(ok)} ok, {len(bad)} failed", "📢")
    shop = await db.get_shop()
    warn = "" if (shop["prices"].get(kind)) else f"\n\n⚠️ No {kind} price set yet – use 💰 Set Price."
    text = (f"✅ **{len(ok)} added**" + ("\n" + "\n".join(f"• {clean(t)}" for t in ok) if ok else "") +
            (f"\n\n❌ **{len(bad)} failed**\n" + "\n".join(f"• {clean(b)}" for b in bad) if bad else "") + warn)
    return await reply(m, text[:4000], [[B("➕ Add more", f"so:sel:{kind}:{sid}", G), B("💰 Prices", "so:price", P)], BACK_HOME])
