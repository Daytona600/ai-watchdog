import json
import os
import sqlite3
from typing import Optional

import httpx

import db

OFF_BASE_URL = os.environ.get("OFF_BASE_URL", "https://world.openfoodfacts.org")
OFF_USER_AGENT = os.environ.get(
    "OFF_USER_AGENT", "InventoryService/0.1 (contact: dstainton55@gmail.com)"
)

FIELDS = "product_name,brands,categories,code,status"


class LookupResult:
    def __init__(self, found: bool, name: Optional[str], brand: Optional[str],
                 category: Optional[str], source: str):
        self.found = found
        self.name = name
        self.brand = brand
        self.category = category
        self.source = source  # "cache" | "open_food_facts" | "miss" | "unreachable"


def _query_off(barcode: str) -> tuple[Optional[dict], Optional[str]]:
    """Returns (parsed_product_dict_or_None, error_reason_or_None)."""
    url = f"{OFF_BASE_URL}/api/v2/product/{barcode}.json"
    try:
        with httpx.Client(timeout=5.0, headers={"User-Agent": OFF_USER_AGENT}) as client:
            resp = client.get(url, params={"fields": FIELDS})
            resp.raise_for_status()
            data = resp.json()
    except (httpx.RequestError, httpx.HTTPStatusError, ValueError) as e:
        return None, str(e)

    if data.get("status") != 1:
        return None, None  # confirmed not found, not an error

    product = data.get("product", {})
    return {
        "name": product.get("product_name") or None,
        "brand": product.get("brands") or None,
        "category": product.get("categories") or None,
        "raw": data,
    }, None


def lookup_barcode(conn: sqlite3.Connection, barcode: str, refresh: bool = False) -> LookupResult:
    if not refresh:
        cached = db.get_barcode_cache(conn, barcode)
        if cached:
            return LookupResult(
                found=bool(cached["found"]),
                name=cached["name"],
                brand=cached["brand"],
                category=cached["category"],
                source="cache",
            )

    product, error = _query_off(barcode)

    if product is not None:
        db.set_barcode_cache(
            conn, barcode, found=True,
            name=product["name"], brand=product["brand"], category=product["category"],
            raw_json=json.dumps(product["raw"]),
        )
        return LookupResult(
            found=True, name=product["name"], brand=product["brand"],
            category=product["category"], source="open_food_facts",
        )

    if error is None:
        # Confirmed miss (OFF answered, product truly not in their database)
        db.set_barcode_cache(conn, barcode, found=False)
        return LookupResult(found=False, name=None, brand=None, category=None, source="open_food_facts")

    # Network/transient failure — do not cache, so it's retried next time
    return LookupResult(found=False, name=None, brand=None, category=None, source="unreachable")
