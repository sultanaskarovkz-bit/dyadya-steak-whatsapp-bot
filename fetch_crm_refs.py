#!/usr/bin/env python3
"""Получение справочников из CRM DelRes"""
import requests, json

BASE = "https://ds-api.delres.kz/api"
TOKEN = "324|cUoHpSjFi8pYtjW2XiMynOLFJCXnS4F6NDV1GqZz761fdd3f"

HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Authorization": f"Bearer {TOKEN}",
}

def get(endpoint, params=None):
    url = f"{BASE}/{endpoint}"
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=15)
        print(f"\n{'='*60}")
        print(f"GET {endpoint} -> {r.status_code}")
        if r.status_code == 200:
            data = r.json()
            print(json.dumps(data, ensure_ascii=False, indent=2)[:5000])
            return data
        else:
            print(f"Error: {r.text[:500]}")
    except Exception as e:
        print(f"Error: {e}")
    return None

print(f"API: {BASE}\n")

print("1. USER")
get("auth/user")

print("\n2. ОРГАНИЗАЦИИ")
get("load-organizations")

print("\n3. TRADE POINTS")
get("load-trade-points", {"organization_id": 1})

print("\n4. ГОРОДА")
get("cities-list")

print("\n5. СПОСОБЫ ОПЛАТЫ")
get("payment-options")

print("\n6. КАНАЛЫ ПРОДАЖ")
get("order/sales-channels")

print("\n7. КАССЫ")
get("cashboxes", {"per_page": 50, "page": 1})

print("\n8. НОМЕНКЛАТУРА")
get("nomenclature-item-balance", {"per_page": 100, "page": 1})

print("\n9. НОМЕНКЛАТУРЫ")
get("nomenclatures", {"per_page": 100, "page": 1})

print("\n10. ЗАКАЗЫ (сегодня)")
get("orders", {"start_date": "2026-02-25", "end_date": "2026-02-26", "per_page": 3, "page": 1})

print("\n11. PURCHASES")
get("purchases", {"startDate": "2026-02-01", "endDate": "2026-02-26", "per_page": 5, "page": 1})

print("\n\nГОТОВО!")
