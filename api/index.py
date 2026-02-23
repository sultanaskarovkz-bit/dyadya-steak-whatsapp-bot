"""
🍔 WhatsApp Bot — Дядя Стейк Бургер
Vercel Serverless + Upstash Redis + Meta Cloud API
"""

import logging
import json
import httpx
from datetime import datetime, timedelta
from fastapi import FastAPI, Request, HTTPException, Query
from fastapi.responses import PlainTextResponse
from upstash_redis import Redis

try:
    from .config import (
        WHATSAPP_TOKEN, WHATSAPP_PHONE_ID, VERIFY_TOKEN,
        TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
        UPSTASH_REDIS_REST_URL, UPSTASH_REDIS_REST_TOKEN,
        BIZ, CATEGORIES, MENU_ITEMS, ITEMS_BY_ID, VARIANTS_BY_ID, t,
    )
except ImportError:
    from config import (
        WHATSAPP_TOKEN, WHATSAPP_PHONE_ID, VERIFY_TOKEN,
        TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
        UPSTASH_REDIS_REST_URL, UPSTASH_REDIS_REST_TOKEN,
        BIZ, CATEGORIES, MENU_ITEMS, ITEMS_BY_ID, VARIANTS_BY_ID, t,
    )

logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="WhatsApp Bot — Дядя Стейк Бургер")

WA_URL = f"https://graph.facebook.com/v22.0/{WHATSAPP_PHONE_ID}/messages"
WA_HEADERS = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}

# ==========================================
# 💾 UPSTASH REDIS
# ==========================================

redis = None
if UPSTASH_REDIS_REST_URL and UPSTASH_REDIS_REST_TOKEN:
    redis = Redis(url=UPSTASH_REDIS_REST_URL, token=UPSTASH_REDIS_REST_TOKEN)


def get_session(phone):
    if redis:
        try:
            data = redis.get(f"session:{phone}")
            if data:
                s = json.loads(data) if isinstance(data, str) else data
                last = datetime.fromisoformat(s.get("last_activity", datetime.now().isoformat()))
                if datetime.now() - last > timedelta(minutes=30):
                    s = new_session(phone)
                s["last_activity"] = datetime.now().isoformat()
                return s
        except Exception as e:
            logger.error(f"Redis get error: {e}")
    return new_session(phone)


def new_session(phone):
    return {
        "phone": phone, "lang": "ru", "state": "new", "cart": [],
        "sel_item": None, "sel_variant": None, "order": {},
        "last_activity": datetime.now().isoformat(),
    }


def save_session(phone, s):
    if redis:
        try:
            redis.set(f"session:{phone}", json.dumps(s, ensure_ascii=False), ex=3600)
        except Exception as e:
            logger.error(f"Redis set error: {e}")


def save_order(s):
    oid = int(datetime.now().strftime("%H%M%S"))
    if redis:
        try:
            order = {
                "id": oid,
                "phone": s["phone"],
                "cart": s["cart"],
                "total": cart_total(s),
                "address": s["order"].get("address", ""),
                "contact_phone": s["order"].get("phone", ""),
                "payment": s["order"].get("payment", ""),
                "comment": s["order"].get("comment", ""),
                "status": "new",
                "created_at": datetime.now().isoformat(),
            }
            redis.set(f"order:{oid}", json.dumps(order, ensure_ascii=False), ex=86400 * 7)
            # Добавляем в список заказов
            redis.lpush("orders:list", str(oid))
        except Exception as e:
            logger.error(f"Redis order save error: {e}")
    return oid


# ==========================================
# 🛒 КОРЗИНА
# ==========================================

def cart_total(s):
    return sum(i["price"] * i["qty"] for i in s.get("cart", []))


def cart_text(s):
    lang = s.get("lang", "ru")
    cart = s.get("cart", [])
    if not cart:
        return t("cart_empty", lang)
    lines = []
    for i, c in enumerate(cart, 1):
        name = c.get(f"name_{lang}", c["name_ru"])
        var = c.get(f"var_{lang}", c["var_ru"])
        lines.append(f"{i}. {name} ({var}) x{c['qty']} — {c['price']*c['qty']:,} тг")
    lines.append(f"\n{t('total', lang)}: *{cart_total(s):,} тг*")
    return "\n".join(lines)


def add_to_cart(s, variant_id, qty=1):
    v = VARIANTS_BY_ID.get(variant_id)
    if not v:
        return
    item = ITEMS_BY_ID[v["item_id"]]
    existing = next((c for c in s["cart"] if c["vid"] == variant_id), None)
    if existing:
        existing["qty"] += qty
    else:
        s["cart"].append({
            "vid": variant_id, "name_ru": item["ru_name"], "name_kz": item["kz_name"],
            "var_ru": v["ru"], "var_kz": v["kz"], "price": v["price"], "qty": qty,
        })


# ==========================================
# 📤 ОТПРАВКА WHATSAPP
# ==========================================

async def send_text(to, text):
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(WA_URL, headers=WA_HEADERS, json={
            "messaging_product": "whatsapp", "to": to, "type": "text",
            "text": {"body": text}
        })
        logger.info(f"📤 send_text -> {r.status_code}")


async def send_buttons(to, text, buttons):
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(WA_URL, headers=WA_HEADERS, json={
            "messaging_product": "whatsapp", "to": to, "type": "interactive",
            "interactive": {
                "type": "button", "body": {"text": text},
                "action": {"buttons": [
                    {"type": "reply", "reply": {"id": b["id"], "title": b["title"][:20]}}
                    for b in buttons[:3]
                ]}
            }
        })
        logger.info(f"📤 send_buttons -> {r.status_code}")


async def send_list(to, text, btn_text, sections):
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(WA_URL, headers=WA_HEADERS, json={
            "messaging_product": "whatsapp", "to": to, "type": "interactive",
            "interactive": {
                "type": "list", "body": {"text": text},
                "action": {"button": btn_text[:20], "sections": sections}
            }
        })
        logger.info(f"📤 send_list -> {r.status_code}")


async def notify_telegram(order_id, s):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    lines = ""
    for c in s["cart"]:
        lines += f"  • {c['name_ru']} ({c['var_ru']}) x{c['qty']} — {c['price']*c['qty']:,} тг\n"
    text = (
        f"🆕 *НОВЫЙ ЗАКАЗ #{order_id}*\n\n"
        f"📱 {s['phone']}\n"
        f"📞 {s['order'].get('phone','—')}\n"
        f"📍 {s['order'].get('address','—')}\n\n"
        f"🛒 *Заказ:*\n{lines}\n"
        f"💰 *Итого: {cart_total(s):,} тг*\n"
        f"💳 {s['order'].get('payment','—')}\n"
        f"💬 {s['order'].get('comment','—')}\n\n"
        f"⏰ {datetime.now().strftime('%H:%M %d.%m.%Y')}"
    )
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            await c.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                         json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"})
    except Exception as e:
        logger.error(f"TG notify failed: {e}")


# ==========================================
# 🧠 ДВИЖОК БОТА
# ==========================================

async def handle(phone, text):
    s = get_session(phone)
    lang = s.get("lang", "ru")
    txt = text.lower().strip()
    state = s["state"]

    # === ГЛОБАЛЬНЫЕ КОМАНДЫ ===
    if txt in ["стоп", "отмена", "stop", "бас тарту"]:
        s = new_session(phone)
        save_session(phone, s)
        await send_text(phone, "❌ Отменено. Напишите *меню* / *мәзір*")
        return

    if txt in ["язык", "тіл", "lang"]:
        s["state"] = "choose_lang"
        save_session(phone, s)
        await send_buttons(phone, "Тілді таңдаңыз / Выберите язык:", [
            {"id": "lang_ru", "title": "🇷🇺 Русский"},
            {"id": "lang_kz", "title": "🇰🇿 Қазақша"},
        ])
        return

    # === ВЫБОР ЯЗЫКА ===
    if state in ["new", "choose_lang"] or txt in ["start", "/start", "привет", "салам", "сәлем", "hello"]:
        if text in ["lang_ru", "🇷🇺 Русский"]:
            s["lang"] = "ru"
            s["state"] = "main"
            save_session(phone, s)
            await show_main(phone, s)
            return
        if text in ["lang_kz", "🇰🇿 Қазақша"]:
            s["lang"] = "kz"
            s["state"] = "main"
            save_session(phone, s)
            await show_main(phone, s)
            return
        s["state"] = "choose_lang"
        save_session(phone, s)
        await send_buttons(phone,
            "Сәлеметсіз бе! 👋 Добро пожаловать!\n🍔 *Дядя Стейк Бургер*\n\nТілді таңдаңыз / Выберите язык:",
            [{"id": "lang_ru", "title": "🇷🇺 Русский"}, {"id": "lang_kz", "title": "🇰🇿 Қазақша"}]
        )
        return

    # === КНОПКИ НАЗАД ===
    if text == "back_main":
        await show_main(phone, s)
        return
    if text == "back_categories":
        await show_categories(phone, s)
        return

    # === ВЫБОР КАТЕГОРИИ (до главного меню!) ===
    if text.startswith("cat_"):
        cat_id = text[4:]
        # Стейки — особая обработка, показываем контакт
        if cat_id == "steaks":
            await send_buttons(phone, t("steaks_contact", lang), [
                {"id": "back_categories", "title": "🔙 " + ("Назад" if lang == "ru" else "Артқа")},
            ])
            s["state"] = "main"
            save_session(phone, s)
            return
        await show_items(phone, s, cat_id)
        return

    # === ВЫБОР ПОЗИЦИИ ===
    if text.startswith("item_"):
        item_id = text[5:]
        await show_item_variants(phone, s, item_id)
        return

    # === ВЫБОР ВАРИАНТА ===
    if text.startswith("var_"):
        vid = text[4:]
        s["sel_variant"] = vid
        s["state"] = "choose_qty"
        save_session(phone, s)
        v = VARIANTS_BY_ID.get(vid)
        item = ITEMS_BY_ID.get(v["item_id"]) if v else None
        name = item.get(f"{lang}_name", item["ru_name"]) if item else ""
        await send_buttons(phone, f"*{name}*\n💰 {v['price']:,} тг\n\n{t('choose_qty', lang)}", [
            {"id": "qty_1", "title": "1 шт"},
            {"id": "qty_2", "title": "2 шт"},
            {"id": "qty_3", "title": "3 шт"},
        ])
        return

    # === FAQ ===
    if text.startswith("faq_"):
        key = text
        if key in ["faq_hours", "faq_delivery", "faq_payment"]:
            await send_text(phone, t(key, lang))
        s["state"] = "main"
        save_session(phone, s)
        return

    # === КОЛИЧЕСТВО ===
    if state == "choose_qty" and (text.startswith("qty_") or txt.isdigit()):
        qty = int(text.replace("qty_", "")) if "qty_" in text else int(txt)
        qty = max(1, min(qty, 20))
        vid = s.get("sel_variant")
        if vid:
            add_to_cart(s, vid, qty)
            v = VARIANTS_BY_ID.get(vid)
            item = ITEMS_BY_ID.get(v["item_id"]) if v else None
            name = item.get(f"{lang}_name", item["ru_name"]) if item else ""
            total = cart_total(s)
            s["state"] = "main"
            save_session(phone, s)
            msg = t("added", lang).format(name=name, qty=qty, total=f"{total:,}")
            min_ok = total >= BIZ["min_order"]
            if lang == "ru":
                msg += "\n\nВыберите ещё что-нибудь или перейдите в корзину 👇"
            else:
                msg += "\n\nТағы бірдеңе таңдаңыз немесе себетке өтіңіз 👇"
            buttons = [
                {"id": "btn_menu", "title": "📋" + (" Ещё" if lang == "ru" else " Тағы")},
                {"id": "btn_cart", "title": "🛒" + (" Корзина" if lang == "ru" else " Себет")},
            ]
            if min_ok:
                buttons.append({"id": "checkout", "title": "✅" + (" Оформить" if lang == "ru" else " Тапсырыс")})
            await send_buttons(phone, msg, buttons)
        return

    # === КОРЗИНА ===
    if txt in ["корзина", "себет", "cart"] or text == "btn_cart":
        await show_cart(phone, s)
        return

    if text == "clear_cart":
        s["cart"] = []
        s["state"] = "main"
        save_session(phone, s)
        await send_text(phone, t("cart_empty", lang))
        return

    # === ОФОРМЛЕНИЕ ===
    if text == "checkout":
        total = cart_total(s)
        if total < BIZ["min_order"]:
            min_val = f"{BIZ['min_order']:,}"
            await send_text(phone, t("min_warn", lang).format(min=min_val))
            return
        s["state"] = "ask_address"
        s["order"] = {}
        save_session(phone, s)
        await send_text(phone, f"{t('cart_title', lang)}\n\n{cart_text(s)}\n\n{t('ask_address', lang)}")
        return

    if state == "ask_address":
        if len(text) < 5:
            await send_text(phone, t("ask_address", lang))
            return
        s["order"]["address"] = text
        s["state"] = "ask_phone"
        save_session(phone, s)
        await send_text(phone, t("ask_phone", lang))
        return

    if state == "ask_phone":
        s["order"]["phone"] = text
        s["state"] = "ask_payment"
        save_session(phone, s)
        await send_buttons(phone, t("ask_payment", lang), [
            {"id": "pay_kaspi", "title": "💳 " + t("pay_kaspi", lang)[:17]},
            {"id": "pay_cash", "title": "💵 " + t("pay_cash", lang)[:17]},
            {"id": "pay_qr", "title": "📱 " + t("pay_qr", lang)[:17]},
        ])
        return

    if state == "ask_payment":
        pay_map = {
            "pay_kaspi": t("pay_kaspi", lang),
            "pay_cash": t("pay_cash", lang),
            "pay_qr": t("pay_qr", lang),
        }
        s["order"]["payment"] = pay_map.get(text, text)
        s["state"] = "ask_comment"
        save_session(phone, s)
        await send_buttons(phone, t("ask_comment", lang), [
            {"id": "cm_none", "title": t("no_comment", lang)[:20]},
            {"id": "cm_noonion", "title": t("no_onion", lang)[:20]},
            {"id": "cm_sauce", "title": t("more_sauce", lang)[:20]},
        ])
        return

    if state == "ask_comment":
        cm_map = {
            "cm_none": "—",
            "cm_noonion": t("no_onion", lang),
            "cm_sauce": t("more_sauce", lang),
        }
        s["order"]["comment"] = cm_map.get(text, text)
        s["state"] = "confirm"
        save_session(phone, s)
        msg = t("confirm", lang).format(
            cart=cart_text(s), addr=s["order"]["address"],
            phone=s["order"]["phone"], pay=s["order"]["payment"],
            comment=s["order"]["comment"], time=BIZ["delivery_time"]
        )
        confirm_title = "✅ Подтверждаю" if lang == "ru" else "✅ Растаймын"
        cancel_title = "❌ Отменить" if lang == "ru" else "❌ Бас тарту"
        await send_buttons(phone, msg, [
            {"id": "confirm_yes", "title": confirm_title[:20]},
            {"id": "confirm_no", "title": cancel_title[:20]},
        ])
        return

    if state == "confirm":
        if text == "confirm_yes":
            oid = save_order(s)
            msg = t("order_done", lang).format(id=oid, time=BIZ["delivery_time"])
            await send_text(phone, msg)
            await notify_telegram(oid, s)
            s["cart"] = []
            s["order"] = {}
            s["state"] = "main"
            save_session(phone, s)
            return
        elif text == "confirm_no":
            s["cart"] = []
            s["order"] = {}
            s["state"] = "main"
            save_session(phone, s)
            await send_text(phone, t("order_cancel", lang))
            return

    # === ГЛАВНОЕ МЕНЮ (после всех конкретных обработчиков) ===
    if txt in ["меню", "мәзір", "menu"] or text == "btn_menu":
        await show_categories(phone, s)
        return

    if text == "btn_faq":
        await show_faq(phone, s)
        return

    if text == "btn_contacts":
        await send_text(phone, t("contacts", lang))
        s["state"] = "main"
        save_session(phone, s)
        return

    # === ПО УМОЛЧАНИЮ ===
    await show_main(phone, s)


# ==========================================
# UI ФУНКЦИИ
# ==========================================

async def show_main(phone, s):
    lang = s.get("lang", "ru")
    s["state"] = "main"
    save_session(phone, s)
    menu_label = "📋 Меню" if lang == "ru" else "📋 Мәзір"
    faq_label = "❓ Вопросы" if lang == "ru" else "❓ Сұрақтар"
    contact_label = "📞 Контакты" if lang == "ru" else "📞 Байланыс"
    await send_buttons(phone, t("main_menu", lang), [
        {"id": "btn_menu", "title": menu_label},
        {"id": "btn_faq", "title": faq_label},
        {"id": "btn_contacts", "title": contact_label},
    ])


async def show_categories(phone, s):
    lang = s.get("lang", "ru")
    s["state"] = "main"
    save_session(phone, s)
    rows = []
    for c in CATEGORIES:
        count = len([i for i in MENU_ITEMS if i['cat'] == c['id']])
        if c['id'] == 'steaks':
            desc = "Свяжитесь с нами" if lang == "ru" else "Бізбен байланысыңыз"
        else:
            desc = f"{count} " + ("позиций" if lang == "ru" else "тағам")
        rows.append({"id": f"cat_{c['id']}", "title": c[lang][:24], "description": desc})
    # Добавляем "Назад" в конец
    rows.append({"id": "back_main", "title": "🔙 " + ("Назад" if lang == "ru" else "Артқа")})
    sections = [{"title": "📋 " + ("Меню" if lang == "ru" else "Мәзір"), "rows": rows}]
    btn = "Открыть меню" if lang == "ru" else "Мәзірді ашу"
    await send_list(phone, t("choose_category", lang), btn, sections)


async def show_items(phone, s, cat_id):
    lang = s.get("lang", "ru")
    items = [i for i in MENU_ITEMS if i["cat"] == cat_id]
    cat = next((c for c in CATEGORIES if c["id"] == cat_id), None)
    cat_name = cat[lang] if cat else ""

    rows = []
    for item in items:
        name = item.get(f"{lang}_name", item["ru_name"])
        prices = [v["price"] for v in item["variants"]]
        price_str = f"{min(prices):,}" if min(prices) == max(prices) else f"от {min(prices):,}"
        rows.append({
            "id": f"item_{item['id']}",
            "title": f"{name}"[:24],
            "description": f"{price_str} тг"[:72],
        })
    # Кнопка назад к категориям
    rows.append({"id": "back_categories", "title": "🔙 " + ("Назад к меню" if lang == "ru" else "Мәзірге қайту")})

    sections = [{"title": cat_name[:24], "rows": rows}]
    btn = "Выбрать" if lang == "ru" else "Таңдау"
    await send_list(phone, f"*{cat_name}*", btn, sections)
    s["state"] = "browse"
    save_session(phone, s)


async def show_item_variants(phone, s, item_id):
    lang = s.get("lang", "ru")
    item = ITEMS_BY_ID.get(item_id)
    if not item:
        return

    name = item.get(f"{lang}_name", item["ru_name"])
    desc = item.get(f"{lang}_desc", item["ru_desc"])
    note = item.get(f"note_{lang}", item.get("note_ru", ""))

    if len(item["variants"]) == 1:
        v = item["variants"][0]
        s["sel_variant"] = v["id"]
        s["state"] = "choose_qty"
        save_session(phone, s)
        text = f"*{name}*\n{desc}\n💰 *{v['price']:,} тг*"
        if note:
            text += f"\n📎 {note}"
        text += f"\n\n{t('choose_qty', lang)}"
        await send_buttons(phone, text, [
            {"id": "qty_1", "title": "1 шт"},
            {"id": "qty_2", "title": "2 шт"},
            {"id": "qty_3", "title": "3 шт"},
        ])
    else:
        text = f"*{name}*\n{desc}"
        if note:
            text += f"\n📎 {note}"
        rows = []
        for v in item["variants"]:
            v_name = v.get(lang, v["ru"])
            rows.append({
                "id": f"var_{v['id']}",
                "title": f"{v_name}"[:24],
                "description": f"{v['price']:,} тг"[:72],
            })
        # Кнопка назад к категории
        rows.append({"id": f"cat_{item['cat']}", "title": "🔙 " + ("Назад" if lang == "ru" else "Артқа")})
        sections = [{"title": name[:24], "rows": rows}]
        btn = "Выбрать" if lang == "ru" else "Таңдау"
        await send_list(phone, text, btn, sections)
        s["state"] = "browse"
        save_session(phone, s)


async def show_cart(phone, s):
    lang = s.get("lang", "ru")
    cart = s.get("cart", [])
    if not cart:
        s["state"] = "main"
        save_session(phone, s)
        await send_text(phone, t("cart_empty", lang))
        return

    text = f"{t('cart_title', lang)}\n\n{cart_text(s)}"
    total = cart_total(s)

    checkout_label = "✅ Оформить" if lang == "ru" else "✅ Тапсырыс"
    menu_label = "➕ Ещё" if lang == "ru" else "➕ Тағы"
    clear_label = "🗑 Очистить" if lang == "ru" else "🗑 Тазалау"

    if total >= BIZ["min_order"]:
        await send_buttons(phone, text, [
            {"id": "checkout", "title": checkout_label},
            {"id": "btn_menu", "title": menu_label},
            {"id": "clear_cart", "title": clear_label},
        ])
    else:
        min_val = f"{BIZ['min_order']:,}"
        text += f"\n\n{t('min_warn', lang).format(min=min_val)}"
        await send_buttons(phone, text, [
            {"id": "btn_menu", "title": menu_label},
            {"id": "clear_cart", "title": clear_label},
        ])
    s["state"] = "main"
    save_session(phone, s)


async def show_faq(phone, s):
    lang = s.get("lang", "ru")
    s["state"] = "main"
    save_session(phone, s)
    rows = [
        {"id": "faq_hours", "title": "🕐 " + ("Время работы" if lang == "ru" else "Жұмыс уақыты")},
        {"id": "faq_delivery", "title": "🚚 " + ("Доставка" if lang == "ru" else "Жеткізу")},
        {"id": "faq_payment", "title": "💳 " + ("Оплата" if lang == "ru" else "Төлем")},
    ]
    sections = [{"title": "FAQ", "rows": rows}]
    title = "❓ Частые вопросы" if lang == "ru" else "❓ Сұрақтар"
    btn = "Выбрать" if lang == "ru" else "Таңдау"
    await send_list(phone, title, btn, sections)


# ==========================================
# 🔗 WEBHOOK ENDPOINTS
# ==========================================

@app.get("/webhook")
async def verify(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
):
    if hub_mode == "subscribe" and hub_verify_token == VERIFY_TOKEN:
        logger.info("✅ Webhook verified!")
        return PlainTextResponse(content=hub_challenge)
    raise HTTPException(403)


@app.post("/webhook")
async def webhook(request: Request):
    try:
        body = await request.json()
        if body.get("object") != "whatsapp_business_account":
            return {"status": "ok"}

        for entry in body.get("entry", []):
            for change in entry.get("changes", []):
                for msg in change.get("value", {}).get("messages", []):
                    phone = msg.get("from")
                    msg_type = msg.get("type")

                    text = ""
                    if msg_type == "text":
                        text = msg["text"]["body"]
                    elif msg_type == "interactive":
                        inter = msg["interactive"]
                        if inter.get("type") == "button_reply":
                            text = inter["button_reply"]["id"]
                        elif inter.get("type") == "list_reply":
                            text = inter["list_reply"]["id"]

                    if text and phone:
                        logger.info(f"💬 [{phone}]: {text}")
                        await handle(phone, text)

        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Webhook error: {e}", exc_info=True)
        return {"status": "error"}


@app.get("/health")
async def health():
    return {"status": "ok", "bot": "Дядя Стейк Бургер WhatsApp Bot", "redis": redis is not None}


@app.get("/")
async def root():
    return {"status": "ok", "message": "🍔 Дядя Стейк Бургер WhatsApp Bot is running!"}
