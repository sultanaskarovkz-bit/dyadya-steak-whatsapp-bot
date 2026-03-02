"""
🔗 CRM Integration — DelRes ERP (ds-api.delres.kz)
Автоматическая отправка заказов из WhatsApp бота в CRM
"""

import os
import re
import json
import logging
import httpx
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

ASTANA_TZ = timezone(timedelta(hours=5))

# ==========================================
# ⚙️  НАСТРОЙКИ CRM
# ==========================================

CRM_BASE_URL = os.environ.get("CRM_BASE_URL", "https://ds-api.delres.kz/api/v1")
CRM_TOKEN = os.environ.get("CRM_TOKEN", "")

CRM_ORGANIZATION_ID = 1    # Дядя Стейк
CRM_TRADE_POINT_ID = 1     # Точка - 1
CRM_CITY_ID = 1            # Тараз
CRM_SALES_CHANNEL_ID = 1   # WhatsApp

# ==========================================
# 💰 МАППИНГ ОПЛАТЫ  (бот → CRM cashbox_id)
# ==========================================

CRM_PAYMENT_MAP = {
    "Нал":   {"id": 1, "payment_type": "cash"},
    "нал":   {"id": 1, "payment_type": "cash"},
    "Kaspi": {"id": 5, "payment_type": "kaspi"},
    "kaspi": {"id": 5, "payment_type": "kaspi"},
    "Каспи": {"id": 5, "payment_type": "kaspi"},
}

# ==========================================
# 🍔 МАППИНГ ТОВАРОВ  (bot variant_id → CRM nomenclature)
# ==========================================

CRM_PRODUCT_MAP = {
    # БУРГЕРЫ (category_id=2)
    "b1_beef": {"crm_id": 48, "cat": 2, "title": "Дядя сырный (говядина)"},
    "b1_chkn": {"crm_id": 58, "cat": 2, "title": "Дядя сырный (курица)"},
    "b2_beef": {"crm_id": 47, "cat": 2, "title": "Дядя грибной (говядина)"},
    "b2_chkn": {"crm_id": 59, "cat": 2, "title": "Дядя грибной (курица)"},
    "b3_beef": {"crm_id": 46, "cat": 2, "title": "Дядя классический (говядина)"},
    "b3_chkn": {"crm_id": 60, "cat": 2, "title": "Дядя классический (курица)"},

    # ХОТ-ДОГИ (category_id=3)
    "h1_firm": {"crm_id": 50, "cat": 3, "title": "Дядя дог-грибной"},
    "h1_smok": {"crm_id": 88, "cat": 3, "title": "Дядя дог-грибной (копченая колбаска)"},
    "h2_firm": {"crm_id": 51, "cat": 3, "title": "Дядя дог"},
    "h2_smok": {"crm_id": 89, "cat": 3, "title": "Дядя дог (копченая колбаска)"},
    "h3_firm": {"crm_id": 57, "cat": 3, "title": "Дядя дог-французский"},
    "h3_smok": {"crm_id": 87, "cat": 3, "title": "Дядя дог-французский (копченая колбаска)"},

    # ДОНЕРЫ (category_id=5)
    "d1_mix":  {"crm_id": 67, "cat": 5, "title": "Дядя-Тётя донер"},
    "d2_beef": {"crm_id": 68, "cat": 5, "title": "Дядя донер"},
    "d3_chkn": {"crm_id": 69, "cat": 5, "title": "Тётя донер"},

    # КОЛБАСКИ (category_id=6)
    "st3_1": {"crm_id": 66, "cat": 6, "title": "Дядины колбаски (5 шт)"},

    # ЗАКУСКИ (category_id=7)
    "sn1_1": {"crm_id": 61, "cat": 7, "title": "Сырные палочки"},
    "sn2_1": {"crm_id": 62, "cat": 7, "title": "Куринные наггетсы"},
    "sn3_1": {"crm_id": 63, "cat": 7, "title": "Картофель фри"},

    # НАПИТКИ (category_id=9)
    "dr1_1": {"crm_id": 82, "cat": 9, "title": "Coca-Cola 1 л"},
    "dr2_1": {"crm_id": 83, "cat": 9, "title": "Coca-Cola банка"},
    "dr3_1": {"crm_id": 94, "cat": 9, "title": "COCA COLA ZERO 0.450 ЖБ"},
    "dr4_1": {"crm_id": 83, "cat": 9, "title": "Coca-Cola банка"},        # Sprite нет → fallback
    "dr5_1": {"crm_id": 84, "cat": 9, "title": "Coca-Cola стекло"},
    "dr6_1": {"crm_id": 86, "cat": 9, "title": "Fuse Tea 0,5 ананас"},
    "dr7_1": {"crm_id": 85, "cat": 9, "title": "Fuse Tea 0,5 ромашка"},
    "dr8_1": {"crm_id": 91, "cat": 9, "title": "Айран"},

    # ДОБАВКИ (category_id=8)
    "ex1_1": {"crm_id": 78, "cat": 8, "title": "Котлета говяжья"},
    "ex2_1": {"crm_id": 79, "cat": 8, "title": "Котлета куриная"},
    "ex3_1": {"crm_id": 77, "cat": 8, "title": "Сыр 50 гр"},
    "ex4_1": {"crm_id": 76, "cat": 8, "title": "Грибы 30 гр"},
}


# ==========================================
# 📱 ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ==========================================

def normalize_phone(phone: str) -> str:
    """Нормализует телефон в формат +7XXXXXXXXXX"""
    digits = re.sub(r'\D', '', phone)
    if digits.startswith('8') and len(digits) == 11:
        digits = '7' + digits[1:]
    elif len(digits) == 10:
        digits = '7' + digits
    return f"+{digits}"


def parse_address(address: str) -> dict:
    """Парсит адрес: 'Мангилик ел 24, кв 90' → street, building, room"""
    result = {"street": None, "building": None, "entrance": None, "floor": None, "room": None}
    if not address:
        return result
    
    # Ищем кв/квартира
    kv_match = re.search(r'(?:кв\.?|квартира)\s*(\d+)', address, re.IGNORECASE)
    if kv_match:
        result["room"] = kv_match.group(1)
    
    # Ищем подъезд
    pod_match = re.search(r'(?:подъезд|подьезд|под\.?)\s*(\d+)', address, re.IGNORECASE)
    if pod_match:
        result["entrance"] = pod_match.group(1)
    
    # Ищем этаж
    floor_match = re.search(r'(?:этаж|эт\.?)\s*(\d+)', address, re.IGNORECASE)
    if floor_match:
        result["floor"] = floor_match.group(1)
    
    # Убираем кв/подъезд/этаж из строки чтобы найти улицу и дом
    clean = re.sub(r'(?:кв\.?|квартира|подъезд|подьезд|под\.?|этаж|эт\.?)\s*\d+', '', address, flags=re.IGNORECASE)
    clean = re.sub(r'[,\s]+$', '', clean).strip()
    
    # Ищем номер дома (последнее число в оставшейся строке)
    parts = re.match(r'^(.+?)\s+(\d+\S*)\s*$', clean)
    if parts:
        result["street"] = parts.group(1).strip()
        result["building"] = parts.group(2).strip()
    else:
        result["street"] = clean
        result["building"] = "1"
    
    return result


def build_nomenclatures(cart: list) -> list:
    """Превращает корзину бота в массив nomenclatures для CRM"""
    noms = []
    for item in cart:
        if not isinstance(item, dict):
            logger.warning(f"CRM: cart item is not dict: {type(item)} = {item}")
            continue
        vid = item.get("vid", "")
        mapping = CRM_PRODUCT_MAP.get(vid)
        if not mapping:
            logger.warning(f"CRM: нет маппинга для variant_id={vid}, пропускаем")
            continue
        noms.append({
            "id": mapping["crm_id"],
            "amount": item.get("qty", 1) * 1000,
            "category_id": mapping["cat"],
            "title": mapping["title"],
            "promotional": False,
        })
    return noms


def build_payment(payment_method: str, total_tenge: int) -> list:
    """Строит массив payments для CRM"""
    pay_info = CRM_PAYMENT_MAP.get(payment_method, CRM_PAYMENT_MAP["Нал"])
    return [{
        "cashbox_id": pay_info["id"],
        "sum": total_tenge * 100,   # тиын
        "payment_type": pay_info["payment_type"],
    }]


# ==========================================
# 🚀 ОТПРАВКА ЗАКАЗА В CRM
# ==========================================

async def send_order_to_crm(session_data: dict) -> dict:
    """
    Отправляет заказ из бота в CRM DelRes.
    
    session_data:
      - cart: [{vid, qty, price, name_ru, ...}]
      - order: {address, phone, payment, comment}
      - phone: номер WhatsApp
    
    Returns: {"success": bool, "order_id": int, "error": str}
    """
    if not CRM_TOKEN:
        logger.warning("CRM: токен не задан, пропускаем")
        return {"success": False, "error": "CRM_TOKEN not set"}
    
    if not isinstance(session_data, dict):
        logger.error(f"CRM: session_data is {type(session_data)}, not dict")
        return {"success": False, "error": "Invalid session data"}
    
    cart = session_data.get("cart", [])
    order_info = session_data.get("order", {})
    if not isinstance(order_info, dict):
        order_info = {}
    
    logger.warning(f"CRM debug: cart type={type(cart)}, order type={type(order_info)}, cart={str(cart)[:300]}")
    
    nomenclatures = build_nomenclatures(cart)
    if not nomenclatures:
        return {"success": False, "error": "Нет товаров для CRM"}
    
    total = sum(i.get("price", 0) * i.get("qty", 1) for i in cart if isinstance(i, dict))
    
    phone = order_info.get("phone") or session_data.get("phone", "")
    phone = normalize_phone(phone)
    
    address = order_info.get("address", "")
    addr = parse_address(address)
    payment_method = order_info.get("payment", "Нал")
    payments = build_payment(payment_method, total)
    
    comment_parts = []
    if order_info.get("comment"):
        comment_parts.append(order_info["comment"])
    comment_parts.append("📱 WhatsApp бот")
    if address:
        comment_parts.append(f"📍 {address}")
    comment = " | ".join(comment_parts)
    
    import uuid as uuid_mod
    
    payload = {
        "uuid": str(uuid_mod.uuid4()),
        "date": datetime.now(ASTANA_TZ).strftime("%Y-%m-%d %H:%M"),
        "comment": comment,
        "is_fiscal": False,
        "organization_id": CRM_ORGANIZATION_ID,
        "trade_point_id": CRM_TRADE_POINT_ID,
        "sales_channel_id": CRM_SALES_CHANNEL_ID,
        "order_tags": [],
        "payments": payments,
        "nomenclatures": nomenclatures,
        "details": {
            "phone": phone,
            "client_name": order_info.get("name", "WhatsApp клиент"),
            "street": addr["street"],
            "building": addr["building"],
            "entrance": addr["entrance"],
            "floor": addr["floor"],
            "room": addr["room"],
            "city_id": CRM_CITY_ID,
            "coordinates": {
                "latitude": None,
                "longitude": None,
            },
        },
    }
    
    headers = {
        "Authorization": f"Bearer {CRM_TOKEN}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    
    logger.warning(f"CRM PAYLOAD: {json.dumps(payload, ensure_ascii=False, default=str)[:1000]}")
    
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{CRM_BASE_URL}/order/orders",
                json=payload,
                headers=headers,
            )
            
            data = resp.json()
            if isinstance(data, list):
                data = {"success": False, "message": str(data)}
            logger.info(f"CRM response {resp.status_code}: {str(data)[:500]}")
            
            if resp.status_code in (200, 201) and data.get("success"):
                order_data = data.get("data", {})
                order_id = 0
                if isinstance(order_data, dict):
                    if "data" in order_data and isinstance(order_data["data"], dict):
                        order_id = order_data["data"].get("id", 0)
                    else:
                        order_id = order_data.get("id", 0)
                logger.info(f"CRM: заказ создан #{order_id}")
                return {"success": True, "order_id": order_id}
                logger.info(f"CRM: заказ создан #{order_id}")
                return {"success": True, "order_id": order_id}
            else:
                error_msg = data.get("message") or str(data)
                logger.error(f"CRM: ошибка {resp.status_code}: {error_msg}")
                return {"success": False, "error": error_msg, "status": resp.status_code}
                
    except Exception as e:
        import traceback
        logger.error(f"CRM: исключение: {e}\n{traceback.format_exc()}")
        return {"success": False, "error": str(e)}
