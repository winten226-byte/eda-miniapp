# -*- coding: utf-8 -*-
"""
Пошук найвигідніших продуктів в українських супермаркетах.
Магазини: Сільпо, АТБ, Варус, Новус, Траш, Велмарт.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from typing import Any, Callable, Optional

import requests
from bs4 import BeautifulSoup

TIMEOUT = 15
CACHE_TTL = 300  # 5 хвилин: повторні запити не звертаються до сайтів магазинів
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": UA,
    "Accept-Language": "uk-UA,uk;q=0.9,en;q=0.8",
}

_LOCAL = threading.local()
_CACHE: dict[tuple, tuple[float, Any]] = {}
_CACHE_LOCK = threading.Lock()


def _session() -> requests.Session:
    """Одна сесія на потік — повторне використання TCP-з'єднань прискорює пошук."""
    session = getattr(_LOCAL, "session", None)
    if session is None:
        session = requests.Session()
        session.headers.update(HEADERS)
        _LOCAL.session = session
    return session


def _cached(key: tuple, producer: Callable[[], Any]) -> Any:
    now = time.time()
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit and now - hit[0] < CACHE_TTL:
            return hit[1]
    value = producer()
    with _CACHE_LOCK:
        _CACHE[key] = (now, value)
    return value


ATB_MS_ID, ATB_MS_KEY = "11280", "8a3d26adf4277edad53bff5b966f445c"
VARUS_MS_ID, VARUS_MS_KEY = "12138", "468671b168b5ec56b8c34d458bf03f1b"
VARUS_SHOP_ID, VARUS_REGION_ID = 1, 1
SILPO_BRANCH = "1edb6b53-596c-6d06-b5f0-b5ff7ea46636"
SILPO_BRANCHES_URL = "https://sf-ecom-api.silpo.ua/v1/uk/branches"
SILPO_IMG_BASE = "https://images.silpo.ua/v2/products/300x300/webp/"

# Центр Києва — використовується, якщо користувач не вказав геолокацію.
KYIV_CENTER = (50.4501, 30.5234)

# Ендпоінти, через які сайти віддають список магазинів/найближчий магазин.
ATB_STORES_URL = "https://www.atbmarket.com/site/getstoresgeo"
ATB_HOME_URL = "https://www.atbmarket.com/"
VARUS_SHOPS_URL = (
    "https://varus.ua/api/catalog/vue_storefront_catalog_2/tms_shop/_search"
)
NOVUS_SHOPS_URL = "https://novus.ua/api/popup/shops"
NOVUS_HOME_URL = "https://novus.ua/"


@dataclass
class Product:
    store: str
    name: str
    price: float
    url: str
    weight: str = ""
    old_price: Optional[float] = None
    amount_kg: Optional[float] = None
    amount_l: Optional[float] = None
    price_unit: str = "шт"
    sale_type: str = "packed"
    form: str = "свіжий"
    image: str = ""
    on_sale: bool = False
    sale_channel: str = "онлайн"

    @property
    def unit_price(self) -> tuple[Optional[float], str]:
        if self.price_unit == "кг" and self.amount_l is None:
            return self.price, "грн/кг"
        if self.amount_l:
            return self.price / self.amount_l, "грн/л"
        if self.amount_kg:
            return self.price / self.amount_kg, "грн/кг"
        return None, ""

    @property
    def discount_percent(self) -> float:
        if not self.old_price or self.old_price <= self.price:
            return 0.0
        return round((self.old_price - self.price) / self.old_price * 100, 1)

    def to_dict(self) -> dict:
        data = asdict(self)
        up, label = self.unit_price
        data["unit_price"] = round(up, 2) if up is not None else None
        data["unit_label"] = label
        data["discount_percent"] = self.discount_percent
        data["on_sale"] = bool(self.on_sale or self.discount_percent)
        return data


def _request(method: str, url: str, **kwargs) -> requests.Response:
    kwargs.setdefault("timeout", TIMEOUT)
    kwargs.setdefault("headers", HEADERS)
    last_error: Optional[Exception] = None
    for attempt in range(2):
        try:
            resp = _session().request(method, url, **kwargs)
            resp.raise_for_status()
            return resp
        except requests.RequestException as exc:
            last_error = exc
            # HTTP-помилки (4xx) повторювати марно — лише мережеві збої
            if attempt == 0 and not isinstance(exc, requests.HTTPError):
                time.sleep(0.25)
            elif isinstance(exc, requests.HTTPError):
                break
    raise last_error  # type: ignore[misc]


def get(url: str, **kwargs) -> requests.Response:
    return _request("GET", url, **kwargs)


def post(url: str, **kwargs) -> requests.Response:
    return _request("POST", url, **kwargs)


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Відстань між координатами у кілометрах (гаверсинус)."""
    radius = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


def get_silpo_branches() -> list[dict]:
    """Список відділень Сільпо з координатами (кешується на CACHE_TTL)."""

    def load() -> list[dict]:
        resp = get(
            SILPO_BRANCHES_URL,
            headers={**HEADERS, "Accept": "application/json"},
        )
        rows = resp.json().get("items", [])
        branches: list[dict] = []
        for row in rows:
            if not isinstance(row, dict) or not row.get("branchId"):
                continue
            try:
                lat = float(row["latitude"])
                lon = float(row["longitude"])
            except (KeyError, TypeError, ValueError):
                continue
            branches.append(
                {
                    "branch_id": str(row["branchId"]),
                    "city": str(row.get("cityFull") or ""),
                    "address": str(row.get("addressFull") or ""),
                    "latitude": lat,
                    "longitude": lon,
                    # У частини відділень немає онлайн-каталогу (порожні /products).
                    # hasPickup — ознака того, що каталог цього відділення є.
                    "has_pickup": bool(row.get("hasPickup")),
                }
            )
        return branches

    return _cached(("silpo_branches",), load)


def nearest_silpo_branches(lat: float, lon: float, count: int = 3) -> list[dict]:
    """Кілька найближчих відділень Сільпо, відсортованих за відстанню."""
    branches = get_silpo_branches()
    if not branches:
        return []
    # Відділення без hasPickup часто мають порожній каталог товарів.
    candidates = [item for item in branches if item.get("has_pickup")] or branches
    nearest = sorted(
        candidates,
        key=lambda item: distance_km(lat, lon, item["latitude"], item["longitude"]),
    )[: max(1, count)]
    result: list[dict] = []
    for branch in nearest:
        item = dict(branch)
        item["distance_km"] = round(
            distance_km(lat, lon, branch["latitude"], branch["longitude"]), 1
        )
        result.append(item)
    return result


def nearest_silpo_branch(lat: float, lon: float) -> dict | None:
    """Найближче до точки відділення Сільпо (сумісний варіант)."""
    branches = nearest_silpo_branches(lat, lon, 1)
    return branches[0] if branches else None


def find_silpo_branch_by_address(text: str) -> dict | None:
    """Грубий пошук відділення за введеним текстом адреси/району."""
    words = [w for w in re.findall(r"[^\W\d_]+", str(text).lower(), re.UNICODE) if len(w) >= 3]
    if not words:
        return None
    branches = get_silpo_branches()
    candidates = [item for item in branches if item.get("has_pickup")] or branches
    best: tuple[int, dict] | None = None
    for branch in candidates:
        haystack = f"{branch['city']} {branch['address']}".lower()
        score = sum(1 for word in words if word in haystack)
        if score and (best is None or score > best[0]):
            best = (score, branch)
    return best[1] if best else None


def _nearest_item(items: list[dict], lat: float, lon: float) -> dict | None:
    """Один найближчий магазин зі списку координат."""
    if not items:
        return None
    nearest = min(items, key=lambda item: distance_km(lat, lon, item["latitude"], item["longitude"]))
    result = dict(nearest)
    result["distance_km"] = round(
        distance_km(lat, lon, nearest["latitude"], nearest["longitude"]), 1
    )
    return result


def get_atb_cities() -> list[dict]:
    """Міста АТБ з координатами — потрібні, щоб вибрати cityid за гео."""

    def load() -> list[dict]:
        resp = get(ATB_HOME_URL)
        cities: list[dict] = []
        for option in re.findall(r"<option\b[^>]*>", resp.text):
            city_id = re.search(r'data-cityid="(\d+)"', option)
            coord = re.search(r'data-coord="([^"]+)"', option)
            if not (city_id and coord):
                continue
            nums = re.findall(r"-?\d+(?:\.\d+)?", coord.group(1))
            if len(nums) < 2:
                continue
            try:
                lat, lon = float(nums[0]), float(nums[1])
            except ValueError:
                continue
            cities.append({"id": city_id.group(1), "latitude": lat, "longitude": lon})
        return cities

    return _cached(("atb_cities",), load)


def get_atb_nearby_stores(lat: float, lon: float, count: int = 1) -> list[dict]:
    """Магазини АТБ поблизу точки: id магазина саме той, що треба в location."""

    def load() -> list[dict]:
        cities = get_atb_cities()
        # На межі міст найближче за координатами місто може повернути порожній
        # список, тому пробуємо кілька найближчих cityid по черзі.
        candidates = sorted(
            cities,
            key=lambda city: distance_km(lat, lon, city["latitude"], city["longitude"]),
        )
        # Бот орієнтований на Київ: спершу пробуємо саме його cityid.
        if distance_km(lat, lon, *KYIV_CENTER) <= 60:
            candidates.sort(key=lambda city: city["id"] != "395")
        candidates = candidates[:5] or [{"id": "395"}]
        headers = {
            **HEADERS,
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": ATB_HOME_URL,
        }
        for city in candidates:
            try:
                resp = post(
                    ATB_STORES_URL,
                    data={
                        "geoloc": f"{lat},{lon}",
                        "cityid": city["id"],
                        "delivery": "1",
                        "delivery_service": "ipost",
                    },
                    headers=headers,
                )
                payload = resp.json()
            except Exception as exc:  # noqa: BLE001 — пробуємо наступне місто
                print(f"[atb] cityid {city['id']}: {exc}", flush=True)
                continue
            if str(payload.get("res")) not in {"1", "True"} or not payload.get("coordinates"):
                continue
            addresses = {
                option.get("value", ""): option.get_text(" ", strip=True)
                for option in BeautifulSoup(
                    payload.get("optselect") or "", "html.parser"
                ).find_all("option")
            }
            stores: list[dict] = []
            for row in payload["coordinates"]:
                store_id = str(row.get("id") or "").strip()
                try:
                    store_lat, store_lon = float(row["lat"]), float(row["lng"])
                except (KeyError, TypeError, ValueError):
                    continue
                if not store_id:
                    continue
                try:
                    distance = round(float(row.get("dist")) / 1000, 1)
                except (TypeError, ValueError):
                    distance = round(distance_km(lat, lon, store_lat, store_lon), 1)
                stores.append(
                    {
                        "id": store_id,
                        "address": addresses.get(store_id, ""),
                        "latitude": store_lat,
                        "longitude": store_lon,
                        "distance_km": distance,
                    }
                )
            if stores:
                stores.sort(key=lambda item: item["distance_km"])
                return stores[: max(1, count)]
        return []

    return _cached(("atb_nearby", round(lat, 2), round(lon, 2), count), load)


def get_varus_shops() -> list[dict]:
    """Список магазинів Варуса з Elasticsearch сайту (кешується)."""

    def load() -> list[dict]:
        resp = post(
            VARUS_SHOPS_URL,
            json={"size": 250, "query": {"match_all": {}}},
            headers={
                **HEADERS,
                "Accept": "application/json, text/plain, */*",
                "Content-Type": "application/json",
                "Origin": "https://varus.ua",
                "Referer": "https://varus.ua/",
            },
        )
        hits = (resp.json().get("hits") or {}).get("hits") or []
        shops: list[dict] = []
        for hit in hits:
            source = hit.get("_source") or {}
            if str(source.get("status")) in {"0", "False"}:
                continue
            if source.get("is_enabled") is False or source.get("upload_to_web") is False:
                continue
            shop_id = str(source.get("id") or hit.get("_id") or "").strip()
            try:
                shop_lat, shop_lon = float(source["lat"]), float(source["long"])
            except (KeyError, TypeError, ValueError):
                continue
            if not shop_id:
                continue
            shops.append(
                {
                    "id": shop_id,
                    "name": str(source.get("name") or ""),
                    "address": str(source.get("address") or ""),
                    "latitude": shop_lat,
                    "longitude": shop_lon,
                }
            )
        return shops

    return _cached(("varus_shops",), load)


def get_novus_cities() -> list[dict]:
    """Міста Новуса з ID, які приймає API списку магазинів."""

    def load() -> list[dict]:
        resp = get(NOVUS_HOME_URL)
        match = re.search(
            r'"cityList"\s*:\s*(\{.*?\})\s*,\s*"shopList"', resp.text, re.DOTALL
        )
        if not match:
            return [{"id": "1", "name": "Київ"}]
        try:
            items = json.loads(match.group(1)).get("items") or []
        except (ValueError, AttributeError):
            return [{"id": "1", "name": "Київ"}]
        cities = [
            {"id": str(item.get("entity_id")), "name": str(item.get("name") or "")}
            for item in items
            if item.get("entity_id")
        ]
        return cities or [{"id": "1", "name": "Київ"}]

    return _cached(("novus_cities",), load)


def get_novus_shops(lat: float | None = None, lon: float | None = None) -> list[dict]:
    """Магазини Новуса з координатами.

    Поблизу Києва запитуємо тільки `cityId=1`, бо це основна аудиторія
    бота й перший холодний запит інакше триває надто довго.
    """

    kyiv_only = (
        lat is not None
        and lon is not None
        and distance_km(lat, lon, *KYIV_CENTER) <= 60
    )

    def load() -> list[dict]:
        def fetch(city: dict) -> list[dict]:
            try:
                resp = post(
                    NOVUS_SHOPS_URL,
                    data={"cityId": city["id"]},
                    headers={
                        **HEADERS,
                        "Accept": "application/json",
                        "X-Requested-With": "XMLHttpRequest",
                        "Referer": NOVUS_HOME_URL,
                    },
                )
                payload = resp.json()
                if payload.get("errors"):
                    return []
                rows = (payload.get("data") or {}).values()
            except Exception as exc:  # noqa: BLE001 — одне місто не блокує інші
                print(f"[novus] Не вдалося отримати магазини {city.get('name')}: {exc}", flush=True)
                return []
            city_shops: list[dict] = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                try:
                    shop_lat, shop_lon = float(row["lat"]), float(row["lng"])
                except (KeyError, TypeError, ValueError):
                    continue
                shop_id = str(row.get("entity_id") or "").strip()
                if not shop_id:
                    continue
                city_shops.append(
                    {
                        "id": shop_id,
                        "name": str(row.get("name") or ""),
                        "address": str(row.get("address") or city.get("name") or ""),
                        "city": str(city.get("name") or ""),
                        "latitude": shop_lat,
                        "longitude": shop_lon,
                    }
                )
            return city_shops

        cities = (
            [{"id": "1", "name": "Київ"}]
            if kyiv_only
            else get_novus_cities()
        )
        with ThreadPoolExecutor(max_workers=6) as executor:
            groups = list(executor.map(fetch, cities))
        shops: list[dict] = []
        seen: set[str] = set()
        for group in groups:
            for shop in group:
                if shop["id"] in seen:
                    continue
                seen.add(shop["id"])
                shops.append(shop)
        return shops

    return _cached(("novus_shops", "kyiv" if kyiv_only else "all"), load)


def resolve_location(
    lat: float | str | None = None,
    lon: float | str | None = None,
    address: str = "",
) -> dict:
    """Привести гео/адресу до координат і найближчого відділення."""
    address = str(address or "").strip()[:200]
    try:
        lat_f = float(lat) if lat not in (None, "") else None
        lon_f = float(lon) if lon not in (None, "") else None
    except (TypeError, ValueError):
        lat_f = lon_f = None

    source = "geo" if lat_f is not None and lon_f is not None else ""
    if source != "geo":
        branch = find_silpo_branch_by_address(address) if address else None
        if branch:
            lat_f, lon_f = branch["latitude"], branch["longitude"]
            source = "address"
        else:
            lat_f, lon_f = KYIV_CENTER
            source = "center"

    branches = nearest_silpo_branches(lat_f, lon_f, 3)
    result: dict[str, Any] = {
        "lat": lat_f,
        "lon": lon_f,
        "address": address,
        "source": source,
        "branch": branches[0] if branches else None,
        "branches": branches,
    }
    # Для кожної мережі вибираємо найближчий магазин саме за її даними.
    def nearest_from(getter: Callable[[], list[dict]]) -> dict | None:
        shops = getter()
        return _nearest_item(shops, lat_f, lon_f) if shops else None

    for key, finder in (
        ("atb", lambda: (get_atb_nearby_stores(lat_f, lon_f, 1) or [None])[0]),
        ("varus", lambda: nearest_from(get_varus_shops)),
        (
            "novus",
            lambda: nearest_from(lambda: get_novus_shops(lat_f, lon_f)),
        ),
    ):
        try:
            result[key] = finder()
        except Exception as exc:  # noqa: BLE001 — локація однієї мережі не блокує пошук
            print(f"[{key}] Не вдалося вибрати найближчий магазин: {exc}", flush=True)
            result[key] = None
    return result


AMOUNT_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(кг|kg|г|гр|g|л|l|мл|ml)\b",
    re.IGNORECASE,
)

WEIGHT_WORDS = (
    "ваговий", "вагова", "вагове", "вагові", "на вагу", "по вазі",
    "весовой", "весовая", "весовое", "на развес",
)

PACKED_WORDS = ("фасований", "фасована", "фасоване", "штучний", "штучна", "шт")

FORM_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("сушений", ("сушен", "сушон", "в'ялен", "вялен", "дегідрат", "сухий", "суха ", "сухе ")),
    ("солений", ("солон", "засол", "бочков", "розсіл", "рассол")),
    ("маринований", ("маринован", "пікант", "posolsk", "посольск")),
    ("консервований", ("консерв", "стериліз", "ск/б", "ск/банк", "з/б", "у відрі", "у ведрі", "пл/відро", "п/відро")),
    ("квашений", ("квашен", "ferment")),
    ("швидкозаморожений", ("швидкозаморож", "frozen", "морожен")),
    ("свіжий", ("свіж", "тепличн", "салатн", "українськ", "грунтов", "свіжо")),
)


def extract_weight(text: str) -> str:
    match = AMOUNT_RE.search(text or "")
    if not match:
        return ""
    value = match.group(1).replace(",", ".")
    unit = match.group(2).lower()
    if unit in ("кг", "kg"):
        return f"{value} кг"
    if unit in ("г", "гр", "g"):
        return f"{value} г"
    if unit in ("л", "l"):
        return f"{value} л"
    return f"{value} мл"


def parse_amount(text: str) -> tuple[Optional[float], Optional[float]]:
    if not text:
        return None, None
    text = str(text).replace(",", ".").replace("\u00a0", " ").strip()

    kg: Optional[float] = None
    litres: Optional[float] = None

    match = re.search(r"(\d+(?:\.\d+)?)\s*(кг|kg)\b", text, re.IGNORECASE)
    if match:
        kg = float(match.group(1))
    else:
        match = re.search(r"(\d+(?:\.\d+)?)\s*(г|гр|g)\b", text, re.IGNORECASE)
        if match:
            kg = float(match.group(1)) / 1000.0

    match = re.search(r"(\d+(?:\.\d+)?)\s*(л|l)\b", text, re.IGNORECASE)
    if match:
        litres = float(match.group(1))
    else:
        match = re.search(r"(\d+(?:\.\d+)?)\s*(мл|ml)\b", text, re.IGNORECASE)
        if match:
            litres = float(match.group(1)) / 1000.0

    return kg, litres


def detect_form(text: str) -> str:
    lowered = " " + str(text or "").lower().replace("\u00a0", " ") + " "
    for label, words in FORM_RULES:
        if any(word in lowered for word in words):
            return label
    return "свіжий"


def detect_sale_type(
    name: str,
    url: str = "",
    *,
    explicit_weighted: Optional[bool] = None,
    price_unit: str = "шт",
    amount_kg: Optional[float] = None,
    weight_text: str = "",
) -> str:
    if explicit_weighted is True:
        return "weighted"
    if price_unit == "кг":
        return "weighted"
    haystack = f"{name} {url} {weight_text}".lower()
    if any(word in haystack for word in WEIGHT_WORDS):
        return "weighted"
    if re.search(r"\b1[-\s]?gat\b|\b1gat\b|\b1-gatunok\b", haystack):
        return "weighted"
    if explicit_weighted is False:
        return "piece" if amount_kg is None else "packed"
    if re.search(r"[-«\s]1[-\s]?гат(унок)?\b|\b1-й\s+гат", haystack):
        return "weighted"
    if any(word in haystack for word in PACKED_WORDS):
        return "packed"
    if amount_kg is None and not weight_text:
        return "piece"
    return "packed"


def make_product(
    store: str,
    name: str,
    price,
    url: str,
    weight: str = "",
    old_price=None,
    *,
    price_unit: str = "шт",
    explicit_weighted: Optional[bool] = None,
    display_price=None,
    form: Optional[str] = None,
    image: str = "",
    on_sale: bool = False,
    sale_channel: str = "онлайн",
) -> Optional[Product]:
    try:
        price_f = float(price)
    except (TypeError, ValueError):
        return None
    if price_f <= 0:
        return None

    clean_name = " ".join(str(name).split())
    weight_text = str(weight or "").strip()
    if not weight_text or len(weight_text) > 20 or weight_text == clean_name:
        weight_text = extract_weight(clean_name) or weight_text

    kg, litres = parse_amount(weight_text or clean_name)
    _ = display_price

    old_f: Optional[float] = None
    try:
        if old_price is not None:
            old_f = float(old_price)
            if old_f <= price_f:
                old_f = None
    except (TypeError, ValueError):
        old_f = None

    sale_type = detect_sale_type(
        clean_name,
        url,
        explicit_weighted=explicit_weighted,
        price_unit=price_unit,
        amount_kg=kg,
        weight_text=weight_text,
    )
    if sale_type == "weighted":
        price_unit = "кг"
    effective_unit = "кг" if price_unit == "кг" else "шт"

    return Product(
        store=store,
        name=clean_name,
        price=price_f,
        url=url,
        weight=weight_text,
        old_price=old_f,
        amount_kg=kg,
        amount_l=litres,
        price_unit=effective_unit,
        sale_type=sale_type,
        form=form or detect_form(clean_name),
        image=str(image or "").strip(),
        on_sale=bool(on_sale or old_f is not None),
        sale_channel=sale_channel or "онлайн",
    )


def search_silpo(
    query: str,
    limit: int,
    branch_id: str | None = None,
    location: dict | None = None,
) -> list[Product]:
    """Ціни конкретного відділення; без branch_id — типове відділення."""
    branch = (branch_id or SILPO_BRANCH).strip()
    url = f"https://sf-ecom-api.silpo.ua/v1/uk/branches/{branch}/products"
    resp = get(
        url,
        params={"search": query, "limit": limit, "inStock": "true"},
        headers={**HEADERS, "Accept": "application/json"},
    )
    items = resp.json().get("items", [])
    products: list[Product] = []
    for item in items:
        ratio = str(item.get("ratio") or "").lower()
        weighted = bool(item.get("weighted")) or ratio in ("кг", "kg")
        display_price = item.get("displayPrice") if weighted else None
        product = make_product(
            "Сільпо",
            item.get("title", ""),
            item.get("price"),
            f"https://silpo.ua/product/{item.get('slug', '')}",
            item.get("displayRatio") or item.get("ratio") or "",
            item.get("oldPrice"),
            price_unit="кг" if weighted else "шт",
            explicit_weighted=weighted,
            display_price=display_price,
            image=f"{SILPO_IMG_BASE}{item.get('icon', '')}" if item.get("icon") else "",
            on_sale=bool(
                item.get("oldPrice")
                or any(
                    isinstance(p, dict) and p.get("type") == "promo"
                    for p in (item.get("promotions") or [])
                )
            ),
            # Це ціни онлайн-каталогу конкретного відділення, не ціна на полиці.
            sale_channel="онлайн",
        )
        if product:
            products.append(product)
    return products


def _flatten_atb_items(raw) -> list[dict]:
    """АТБ іноді повертає товари списками списків (алкоголь, пиво тощо)."""
    if isinstance(raw, dict):
        return [raw]
    if isinstance(raw, (list, tuple)):
        flat: list[dict] = []
        for item in raw:
            flat.extend(_flatten_atb_items(item))
        return flat
    return []


def search_atb(query: str, limit: int, location: dict | None = None) -> list[Product]:
    atb_shop = (location or {}).get("atb") or {}
    params = {
        "id": ATB_MS_ID,
        "key": ATB_MS_KEY,
        "lang": "uk",
        # Кожен магазин АТБ має власний location у пошуку — ціни відрізняються.
        "location": str(atb_shop.get("id") or "1154"),
        "m": str(int(time.time() * 1000)),
        "q": uuid.uuid4().hex[:6],
        "query": query,
        "s": "mini",
        "uid": str(uuid.uuid4()),
    }
    resp = get(
        "https://api.multisearch.io/",
        params=params,
        headers={
            **HEADERS,
            "Accept": "application/json",
            "Origin": "https://www.atbmarket.com",
            "Referer": "https://www.atbmarket.com/",
        },
    )
    results = resp.json().get("results")
    if not isinstance(results, dict):
        return []

    products: list[Product] = []
    for group in results.get("item_groups") or []:
        if not isinstance(group, dict):
            continue
        for item in _flatten_atb_items(group.get("items", [])):
            if item.get("is_presence") is False:
                continue
            params_data = item.get("params_data")
            if not isinstance(params_data, dict):
                params_data = {}
            raw_weighted = params_data.get("Ваговий") or params_data.get("Весовой")
            explicit_weighted: Optional[bool] = None
            if isinstance(raw_weighted, str):
                explicit_weighted = raw_weighted.strip().lower() in ("так", "yes", "true", "1")

            product = make_product(
                "АТБ",
                item.get("name", ""),
                item.get("price"),
                (item.get("url") or "").split("?")[0],
                item.get("name", ""),
                item.get("oldprice"),
                price_unit="кг" if explicit_weighted else "шт",
                explicit_weighted=explicit_weighted,
                image=item.get("picture") or "",
                on_sale=bool(item.get("oldprice")),
            )
            if product:
                products.append(product)
            if len(products) >= limit:
                return products
    return products


def search_varus(query: str, limit: int, location: dict | None = None) -> list[Product]:
    varus_shop = (location or {}).get("varus") or {}
    try:
        shop_id = int(varus_shop.get("id") or VARUS_SHOP_ID)
    except (TypeError, ValueError):
        shop_id = VARUS_SHOP_ID
    resp = get(
        "https://api.multisearch.io/",
        params={
            "id": VARUS_MS_ID,
            "key": VARUS_MS_KEY,
            "query": query,
            "lang": "uk",
            "categories": "0",
            "limit": str(max(limit, 18)),
            "uid": str(uuid.uuid4()),
        },
        headers={
            **HEADERS,
            "Accept": "application/json",
            "Origin": "https://varus.ua",
            "Referer": "https://varus.ua/",
        },
    )
    results = resp.json().get("results")
    if not isinstance(results, dict):
        return []

    ids: list[str] = []
    for raw_id in results.get("ids") or []:
        product_id = str(raw_id).split("_")[0]
        if product_id.isdigit() and product_id not in ids:
            ids.append(product_id)
    ids = ids[: min(limit, 20)]
    if not ids:
        return []

    fields = (
        "id sku name urlKey weight "
        "regionalPriceInfo{price specialPrice promoPrice} "
        "availability{inStock available}"
    )
    parts = [
        f'p{i}: product(id:"{pid}", shopId:{shop_id}, '
        f"regionId:{VARUS_REGION_ID}){{{fields}}}"
        for i, pid in enumerate(ids)
    ]
    resp = post(
        "https://varus.ua/api/graphql/catalog",
        json={"query": "{" + " ".join(parts) + "}"},
        headers={
            **HEADERS,
            "Content-Type": "application/json",
            "Origin": "https://varus.ua",
            "Referer": "https://varus.ua/",
        },
    )
    data = resp.json().get("data") or {}

    products: list[Product] = []
    for item in data.values():
        if not item:
            continue
        availability = item.get("availability") or {}
        if availability.get("available") is False and availability.get("inStock") is False:
            continue

        price_info = item.get("regionalPriceInfo") or {}
        special = price_info.get("specialPrice") or 0
        promo = price_info.get("promoPrice") or 0
        regular = price_info.get("price") or 0
        price = special or promo or regular
        old_price = regular if (special or promo) and regular > price else None

        weight = item.get("weight")
        weight_text = f"{int(float(weight))} г" if weight and float(weight) > 0 else ""

        product = make_product(
            "Варус",
            item.get("name", ""),
            price,
            f"https://varus.ua/{item.get('urlKey', '')}",
            weight_text,
            old_price,
            image=f"https://varus.ua/img/product/670/670/{item.get('sku', '')}" if item.get("sku") else "",
        )
        if product:
            products.append(product)
    return products


def search_novus(query: str, limit: int, location: dict | None = None) -> list[Product]:
    novus_shop = (location or {}).get("novus") or {}
    shop_id = str(novus_shop.get("id") or "")
    if shop_id:
        # Сайт зберігає обраний магазин у cookie novus_shop.
        session = _session()
        session.cookies.set("novus_shop", shop_id, domain=".novus.ua", path="/")
        resp = session.get(
            "https://novus.ua/catalogsearch/result/",
            params={"q": query},
            headers={**HEADERS, "Accept": "text/html"},
            timeout=TIMEOUT,
        )
    else:
        resp = get("https://novus.ua/catalogsearch/result/", params={"q": query})
    soup = BeautifulSoup(resp.text, "html.parser")

    products: list[Product] = []
    for card in soup.select("li.product-item")[:limit]:
        name_el = card.select_one(".product-item-link")
        link_el = card.select_one("a.product-item-photo")
        price_el = card.select_one('[data-price-type="finalPrice"]')
        old_el = card.select_one('[data-price-type="oldPrice"]')
        if not (name_el and link_el and price_el):
            continue

        name = name_el.get_text(" ", strip=True)
        img_el = card.select_one("img.product-image-photo") or card.find("img")
        image = ""
        if img_el:
            image = img_el.get("src") or img_el.get("data-src") or img_el.get("data-original") or ""
        product = make_product(
            "Новус",
            name,
            price_el.get("data-price-amount"),
            link_el.get("href", ""),
            name,
            old_el.get("data-price-amount") if old_el else None,
            image=image,
        )
        if product:
            products.append(product)
    return products


def search_thrash(query: str, limit: int, location: dict | None = None) -> list[Product]:
    graphql = (
        "{search(query:%s,pagingInfo:{limit:%d,offset:0})"
        "{paging{count items{id slug title price oldPrice weight available imageUrl}}}}"
        % (json.dumps(query, ensure_ascii=False), limit)
    )
    session = requests.Session()
    session.get("https://thrash.ua/", headers=HEADERS, timeout=TIMEOUT)
    resp = session.post(
        "https://thrash.ua/graphql",
        json={"query": graphql},
        headers={
            **HEADERS,
            "Content-Type": "application/json",
            "Origin": "https://thrash.ua",
            "Referer": "https://thrash.ua/",
        },
    )
    search = (resp.json().get("data") or {}).get("search") or {}
    items = (search.get("paging") or {}).get("items") or []

    products: list[Product] = []
    for item in items:
        if item.get("available") is False:
            continue
        product = make_product(
            "Траш",
            item.get("title", ""),
            item.get("price"),
            f"https://thrash.ua/{item.get('slug', '')}",
            item.get("weight") or item.get("title", ""),
            item.get("oldPrice"),
            image=item.get("imageUrl") or "",
        )
        if product:
            products.append(product)
    return products


def _velmart_meta(ids: list[Any]) -> dict[str, dict]:
    """Зображення Велмарта є лише у WP REST (acf.external_image_url)."""
    clean_ids = [str(i) for i in ids if i]
    if not clean_ids:
        return {}

    def load() -> dict[str, dict]:
        try:
            resp = get(
                "https://velmart.ua/wp-json/wp/v2/product",
                params={
                    "include": ",".join(clean_ids),
                    "per_page": len(clean_ids),
                    "_fields": "id,acf",
                },
                headers={"Accept": "application/json"},
                timeout=8,
            )
            data = resp.json()
            if not isinstance(data, list):
                return {}
            return {
                str(row.get("id")): row.get("acf") or {}
                for row in data
                if isinstance(row, dict)
            }
        except Exception:  # noqa: BLE001 — без зображення товар усе одно покажемо
            return {}

    return _cached(("velmart_meta", tuple(clean_ids)), load)


def search_velmart(query: str, limit: int, location: dict | None = None) -> list[Product]:
    resp = get(
        "https://velmart.ua/wp-json/wc/store/products",
        params={"search": query, "per_page": min(max(limit, 1), 100)},
    )
    raw_items = resp.json()
    if not isinstance(raw_items, list):
        return []

    meta = _velmart_meta([item.get("id") for item in raw_items if isinstance(item, dict)])
    products: list[Product] = []
    for item in raw_items:
        if not isinstance(item, dict) or item.get("is_in_stock") is False:
            continue
        prices = item.get("prices") or {}
        if not prices.get("price"):
            continue
        divisor = 10 ** int(prices.get("currency_minor_unit", 2))
        acf = meta.get(str(item.get("id"))) or {}
        images = item.get("images") or []
        image = acf.get("external_image_url") or ""
        if not image and isinstance(images, list) and images and isinstance(images[0], dict):
            image = images[0].get("src") or ""

        product = make_product(
            "Велмарт",
            item.get("name", ""),
            float(prices["price"]) / divisor,
            item.get("permalink", ""),
            item.get("name", ""),
            float(prices["regular_price"]) / divisor if prices.get("regular_price") else None,
            image=image,
            on_sale=bool(item.get("on_sale") or acf.get("promo_start") or acf.get("promo_end")),
        )
        if product:
            products.append(product)
    return products


StoreFunc = Callable[[str, int, dict | None], list[Product]]

STORES: dict[str, tuple[str, StoreFunc]] = {
    "silpo": ("Сільпо", search_silpo),
    "atb": ("АТБ", search_atb),
    "varus": ("Варус", search_varus),
    "novus": ("Новус", search_novus),
    "thrash": ("Траш", search_thrash),
    "velmart": ("Велмарт", search_velmart),
}

ALIASES = {
    "сільпо": "silpo",
    "сильпо": "silpo",
    "атб": "atb",
    "варус": "varus",
    "новус": "novus",
    "траш": "thrash",
    "велмарт": "velmart",
    "вел-март": "velmart",
}


def resolve_stores(value: str) -> list[str]:
    if not value or value.strip().lower() in {"all", "усі", "всі"}:
        return list(STORES)

    result: list[str] = []
    for raw in value.split(","):
        key = raw.strip().lower()
        key = ALIASES.get(key, key)
        if key in STORES and key not in result:
            result.append(key)
        elif key:
            raise SystemExit(f"Невідомий магазин: {raw!r}. Доступні: {', '.join(STORES)}")
    return result


def split_words(text: str) -> list[str]:
    text = str(text).lower()
    for char in ("'", "\u2019", "\u2018", "\u02bc", "`"):
        text = text.replace(char, "")
    return re.findall(r"[^\W\d_]+", text, re.UNICODE)


def same_stem(word: str, other: str) -> bool:
    if not word or not other:
        return False
    if word.startswith("ог") and other.startswith("ог"):
        return True
    if word.startswith("сир") and other.startswith("сир"):
        return True
    common = 0
    for left, right in zip(word, other):
        if left != right:
            break
        common += 1
    required = 3 if min(len(word), len(other)) <= 4 else 4
    return common >= required


def is_relevant(name: str, query: str) -> bool:
    query_words = split_words(query)
    if not query_words:
        return True
    name_words = split_words(name)
    return bool(name_words) and same_stem(name_words[0], query_words[0])


def contains_query(name: str, query: str) -> bool:
    query_words = split_words(query)
    if not query_words:
        return False
    name_words = split_words(name)
    return all(
        any(same_stem(name_word, query_word) for name_word in name_words)
        for query_word in query_words
    )


def find_products(
    query: str,
    store_keys: Optional[list[str]] = None,
    limit: int = 20,
    *,
    filter_mode: str = "auto",
    location: dict | None = None,
) -> tuple[list[Product], list[tuple[str, str]]]:
    store_keys = list(store_keys or STORES)
    all_products: list[Product] = []
    errors: list[tuple[str, str]] = []
    loc = location or {}
    branch_ids = [
        item["branch_id"]
        for item in (loc.get("branches") or [])
        if isinstance(item, dict) and item.get("branch_id")
    ]
    if not branch_ids:
        branch_info = loc.get("branch") or {}
        if branch_info.get("branch_id"):
            branch_ids = [branch_info["branch_id"]]

    # Ключ кешу враховує обраний магазин кожної мережі.
    location_key = (
        tuple(branch_ids),
        str(((loc.get("atb") or {}).get("id")) or ""),
        str(((loc.get("varus") or {}).get("id")) or ""),
        str(((loc.get("novus") or {}).get("id")) or ""),
    )

    def load_store(key: str) -> list[Product]:
        if key == "silpo":
            def load_silpo() -> list[Product]:
                result: list[Product] = []
                for branch_id in branch_ids or [None]:
                    result.extend(search_silpo(query, limit, branch_id, loc))
                return result

            return _cached(("store", key, query, limit, location_key), load_silpo)
        return _cached(
            ("store", key, query, limit, location_key),
            lambda: STORES[key][1](query, limit, loc),
        )

    with ThreadPoolExecutor(max_workers=max(1, len(store_keys))) as executor:
        futures = {
            executor.submit(load_store, key): key
            for key in store_keys
        }
        for future in as_completed(futures):
            key = futures[future]
            store_name = STORES[key][0]
            try:
                all_products.extend(future.result())
            except Exception as exc:  # noqa: BLE001
                errors.append((store_name, f"{type(exc).__name__}: {exc}"))

    unique: list[Product] = []
    seen: set[tuple[str, str, float]] = set()
    for product in all_products:
        key = (product.store, product.name.lower(), round(product.price, 2))
        if key in seen:
            continue
        seen.add(key)
        unique.append(product)

    if filter_mode == "all":
        filtered = unique
    elif filter_mode == "strict":
        filtered = [p for p in unique if is_relevant(p.name, query)]
    else:
        strict_matches = [p for p in unique if is_relevant(p.name, query)]
        stores_found = len({p.store for p in strict_matches})
        if len(strict_matches) >= 5 and stores_found >= 3:
            filtered = strict_matches
        else:
            extra = [
                p
                for p in unique
                if p not in strict_matches and contains_query(p.name, query)
            ]
            filtered = strict_matches + extra
            if not filtered:
                filtered = unique

    filtered.sort(
        key=lambda p: (
            p.unit_price[0] is None,
            p.unit_price[0] if p.unit_price[0] is not None else 0.0,
            p.price,
        )
    )
    return filtered, errors


SALE_CATEGORIES: dict[str, tuple[str, str]] = {
    "all": ("Всі товари", "продукт"),
    "dairy": ("Молочні продукти", "молоко"),
    "meat": ("М'ясо та птиця", "м'ясо"),
    "bakery": ("Хліб та випічка", "хліб"),
    "fruits": ("Фрукти", "яблука"),
    "vegetables": ("Овочі", "помідори"),
    "drinks": ("Напої", "сік"),
    "sweets": ("Солодке", "цукерки"),
    "alcohol": ("Алкоголь", "алкоголь"),
}


def search_sales(
    category: str,
    store_keys: Optional[list[str]] = None,
    limit: int = 20,
    *,
    location: dict | None = None,
) -> tuple[list[Product], list[tuple[str, str]]]:
    """Товари за акцією у вибраній категорії."""
    _, query = SALE_CATEGORIES.get(category, SALE_CATEGORIES["all"])
    products, errors = find_products(
        query or "продукт", store_keys, limit, filter_mode="all", location=location
    )
    sales = [p for p in products if p.on_sale or p.old_price]
    sales.sort(key=lambda p: (-p.discount_percent, p.price))
    return sales, errors


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Пошук продуктів у супермаркетах")
    parser.add_argument("query", nargs="+")
    parser.add_argument("--stores", default="all")
    parser.add_argument("--limit", type=int, default=15)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    q = " ".join(args.query).strip()
    found, errs = find_products(q, resolve_stores(args.stores), max(1, args.limit))
    if args.json:
        print(json.dumps([p.to_dict() for p in found], ensure_ascii=False, indent=2))
    else:
        for idx, item in enumerate(found[:20], 1):
            up, lbl = item.unit_price
            up_str = f" ({up:.2f} {lbl})" if up else ""
            print(f"{idx}. [{item.store}] {item.name} — {item.price:.2f} грн{up_str}")
        if errs:
            print("Помилки:", errs)