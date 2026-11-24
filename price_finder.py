# -*- coding: utf-8 -*-
"""
Поиск самых дешёвых продуктов в украинских супермаркетах.
Магазины: Сільпо, АТБ, Варус, Новус, Траш, Велмарт.
"""
from __future__ import annotations

import argparse
import json
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
CACHE_TTL = 300  # 5 минут: повторные запросы не дёргают сайты магазинов
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
    """Одна сессия на поток — переиспользование TCP-соединений ускоряет поиск."""
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
SILPO_IMG_BASE = "https://images.silpo.ua/v2/products/300x300/webp/"


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
            # HTTP-ошибки (4xx) повторять бессмысленно — только сетевые сбои
            if attempt == 0 and not isinstance(exc, requests.HTTPError):
                time.sleep(0.25)
            elif isinstance(exc, requests.HTTPError):
                break
    raise last_error  # type: ignore[misc]


def get(url: str, **kwargs) -> requests.Response:
    return _request("GET", url, **kwargs)


def post(url: str, **kwargs) -> requests.Response:
    return _request("POST", url, **kwargs)


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
    )


def search_silpo(query: str, limit: int) -> list[Product]:
    url = f"https://sf-ecom-api.silpo.ua/v1/uk/branches/{SILPO_BRANCH}/products"
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
        )
        if product:
            products.append(product)
    return products


def _flatten_atb_items(raw) -> list[dict]:
    """АТБ иногда отдаёт товары списками списков (алкоголь, пиво и т.п.)."""
    if isinstance(raw, dict):
        return [raw]
    if isinstance(raw, (list, tuple)):
        flat: list[dict] = []
        for item in raw:
            flat.extend(_flatten_atb_items(item))
        return flat
    return []


def search_atb(query: str, limit: int) -> list[Product]:
    params = {
        "id": ATB_MS_ID,
        "key": ATB_MS_KEY,
        "lang": "uk",
        "location": "498",
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


def search_varus(query: str, limit: int) -> list[Product]:
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
        f'p{i}: product(id:"{pid}", shopId:{VARUS_SHOP_ID}, '
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


def search_novus(query: str, limit: int) -> list[Product]:
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


def search_thrash(query: str, limit: int) -> list[Product]:
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
    """Картинки Велмарта есть только в WP REST (acf.external_image_url)."""
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
        except Exception:  # noqa: BLE001 — без картинки товар всё равно покажем
            return {}

    return _cached(("velmart_meta", tuple(clean_ids)), load)


def search_velmart(query: str, limit: int) -> list[Product]:
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


StoreFunc = Callable[[str, int], list[Product]]

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
) -> tuple[list[Product], list[tuple[str, str]]]:
    store_keys = list(store_keys or STORES)
    all_products: list[Product] = []
    errors: list[tuple[str, str]] = []

    def load_store(key: str) -> list[Product]:
        return _cached(
            ("store", key, query, limit),
            lambda: STORES[key][1](query, limit),
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
) -> tuple[list[Product], list[tuple[str, str]]]:
    """Товары по акции в выбранной категории."""
    _, query = SALE_CATEGORIES.get(category, SALE_CATEGORIES["all"])
    products, errors = find_products(
        query or "продукт", store_keys, limit, filter_mode="all"
    )
    sales = [p for p in products if p.on_sale or p.old_price]
    sales.sort(key=lambda p: (-p.discount_percent, p.price))
    return sales, errors


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Поиск продуктов в супермаркетах")
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
            print("Ошибки:", errs)