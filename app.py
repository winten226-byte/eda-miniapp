# -*- coding: utf-8 -*-
"""
Веб-сервер та інтерфейс Telegram Mini App для пошуку найвигідніших продуктів.

Запуск:
    python app.py
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

from price_finder import SALE_CATEGORIES, STORES, find_products, search_sales

HOST = "0.0.0.0"
PORT = 8000
PAGE_SIZE = 12

STORE_LABELS = {key: value[0] for key, value in STORES.items()}

FORM_HINTS = {
    "свіжий": ("свіжий", "hint-fresh"),
    "солений": ("солений", "hint-salted"),
    "маринований": ("маринований", "hint-marinated"),
    "консервований": ("консерви", "hint-other"),
    "квашений": ("квашений", "hint-other"),
    "сушений": ("сушений", "hint-dried"),
    "швидкозаморожений": ("заморожений", "hint-other"),
}

SALE_LABELS = {
    "weighted": "на вагу",
    "piece": "штучно",
    "packed": "упаковка",
}
SALE_CLASS = {
    "weighted": "tag-weight",
    "piece": "tag-piece",
    "packed": "tag-packed",
}


def local_ip() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return ""
    finally:
        sock.close()


def esc(text) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def price_text(product: dict) -> str:
    if product.get("price_unit") == "кг":
        return f"{product['price']:.2f} <small>грн/кг</small>"
    return f"{product['price']:.2f} <small>грн</small>"


def render_card(product: dict) -> str:
    unit_price = product.get("unit_price")
    if unit_price is not None:
        up_html = (
            "<span class='unit'>"
            f"{unit_price:.2f} {esc(product.get('unit_label'))}"
            "</span>"
        )
    else:
        up_html = "<span class='unit unit-muted'>ціна за кг не вказана</span>"

    old = product.get("old_price")
    old_html = f"<span class='old'>{old:.2f} грн</span>" if old is not None else ""
    discount = product.get("discount_percent") or 0
    discount_html = f"<span class='discount'>−{discount:g}%</span>" if discount else ""
    action_badge = (
        "<span class='tag tag-action'>акція</span>"
        if product.get("on_sale") and not discount else ""
    )

    sale = product.get("sale_type", "packed")
    form = product.get("form", "свіжий")
    form_label, form_class = FORM_HINTS.get(form, (form, "hint-other"))

    weight = product.get("weight") or ""
    weight_html = f"<span class='weight'>{esc(weight)}</span>" if weight else ""

    image = product.get("image") or ""
    if image:
        img_html = (
            f"<img src='{esc(image)}' alt='{esc(product['name'])}' loading='lazy' decoding='async' "
            "onerror=\"this.style.display='none';this.nextElementSibling.style.display='grid'\">"
            "<span class='img-fallback' aria-hidden='true'>🛍️</span>"
        )
    else:
        img_html = "<span class='img-fallback' style='display:grid' aria-hidden='true'>🛍️</span>"

    url = product.get("url") or ""
    if url:
        link = (
            f"<a class='go' data-ext-url='{esc(url)}' href='{esc(url)}' "
            "target='_blank' rel='noopener noreferrer' title='Відкрити товар у магазині'>↗</a>"
        )
    else:
        link = "<span class='go go-muted' title='Посилання немає'>↗</span>"

    return f"""
    <article class="card">
      <div class="thumb">{img_html}</div>
      <div class="body">
        <div class="tags">
          <span class="store">{esc(product['store'])}</span>
          <span class="tag {SALE_CLASS.get(sale, 'tag-packed')}">{esc(SALE_LABELS.get(sale, sale))}</span>
          <span class="hint {form_class}">{esc(form_label)}</span>
          {action_badge}
        </div>
        <div class="name" title="{esc(product['name'])}">{esc(product['name'])}</div>
        <div class="meta">{weight_html}{up_html}</div>
        <div class="bottom">
          <div class="prices">{price_text(product)}{old_html}{discount_html}</div>
          {link}
        </div>
      </div>
    </article>
    """


def render_page(
    query: str = "",
    products: list[dict] | None = None,
    errors: list[tuple[str, str]] | None = None,
    selected_stores: list[str] | None = None,
    sale_filter: str = "all",
    sort_by: str = "unit",
    searched: bool = False,
    limit: int = 20,
    active_tab: str = "search",
    category: str = "dairy",
) -> str:
    products = products or []
    errors = errors or []
    if selected_stores is None:
        selected_stores = list(STORES)

    store_checks = "".join(
        f"""<label class="chip"><input type="checkbox" name="stores" value="{key}"
        {'checked' if key in selected_stores else ''}><span>{esc(label)}</span></label>"""
        for key, label in STORE_LABELS.items()
    )

    def sel(value: str, current: str) -> str:
        return "selected" if value == current else ""

    sale_options = "".join(
        f"<option value='{k}' {sel(k, sale_filter)}>{esc(v)}</option>"
        for k, v in [("all", "усі"), *SALE_LABELS.items()]
    )
    sort_options = "".join(
        f"<option value='{k}' {sel(k, sort_by)}>{v}</option>"
        for k, v in [
            ("unit", "ціна за кг/л"),
            ("price", "від найнижчої ціни до найвищої"),
            ("discount", "найбільша знижка"),
            ("name", "назва"),
        ]
    )
    category_options = "".join(
        f"<option value='{k}' {sel(k, category)}>{esc(label)}</option>"
        for k, (label, _) in SALE_CATEGORIES.items()
    )

    shown = [
        p
        for p in products
        if sale_filter == "all" or p.get("sale_type") == sale_filter
    ]
    if sort_by == "price":
        shown.sort(key=lambda p: (p.get("price") or 0, p.get("unit_price") or 0))
    elif sort_by == "discount":
        shown.sort(
            key=lambda p: (
                -(p.get("discount_percent") or 0),
                p.get("price") or 0,
            )
        )
    elif sort_by == "name":
        shown.sort(key=lambda p: p.get("name", "").lower())
    else:
        shown.sort(
            key=lambda p: (
                p.get("unit_price") is None,
                p.get("unit_price") or 0,
                p.get("price") or 0,
            )
        )

    cards = "\n".join(render_card(p) for p in shown)
    if searched and not shown:
        if active_tab == "sales":
            cards = (
                "<div class='empty'><div class='empty-ico'>🏷️</div>"
                "<h3>Акцій не знайдено</h3>"
                "<p>Спробуйте іншу категорію або виберіть більше магазинів.</p></div>"
            )
        else:
            cards = (
                "<div class='empty'><div class='empty-ico'>🔎</div>"
                "<h3>Нічого не знайшлося</h3>"
                "<p>Спробуйте інший запит або виберіть більше магазинів.</p></div>"
            )

    more_button = ""
    if len(shown) > PAGE_SIZE:
        more_button = (
            f"<button class='more' id='more' type='button'>Показати ще "
            f"({len(shown) - PAGE_SIZE})</button>"
        )

    errors_html = ""
    if errors:
        items = "".join(
            f"<li><b>{esc(store)}</b>: {esc(msg)}</li>" for store, msg in errors
        )
        errors_html = (
            "<div class='alert'><b>Не вдалося отримати дані:</b>"
            f"<ul>{items}</ul></div>"
        )

    summary = ""
    if searched:
        what = "акційних товарів" if active_tab == "sales" else "товарів"
        summary = (
            "<div class='summary'>"
            f"<b>{len(shown)}</b> {what} · <b>{len(selected_stores)}</b> магазинів"
            "</div>"
        )

    search_form = f"""
    <form class="search" id="search-form" method="get" action="/">
      <input type="hidden" name="tab" value="search">
      <div class="line">
        <div class="input-wrap">
          <span class="ico">🔍</span>
          <input type="text" name="q" value="{esc(query)}"
                 placeholder="Наприклад: молоко, огірок, банани">
        </div>
        <select name="sale" title="Тип продажу">{sale_options}</select>
        <select name="sort" title="Сортування">{sort_options}</select>
        <input type="hidden" name="limit" value="{limit}">
        <button class="submit" id="submit-btn" type="submit">Знайти</button>
      </div>
      <div class="chips">{store_checks}</div>
    </form>
    """

    sales_form = f"""
    <form class="search" id="sales-form" method="get" action="/">
      <input type="hidden" name="tab" value="sales">
      <div class="line">
        <select name="category" title="Категорія">{category_options}</select>
        <select name="sort" title="Сортування">{sort_options}</select>
        <input type="hidden" name="limit" value="{limit}">
        <button class="submit" id="sales-btn" type="submit">Показати акції</button>
      </div>
      <div class="chips">{store_checks}</div>
    </form>
    """

    feedback_form = """
    <form class="search feedback" id="feedback-form">
      <div class="line">
        <select id="feedback-category" name="category" title="Тип повідомлення">
          <option value="idea">💡 Ідея</option>
          <option value="bug">🐛 Несправність</option>
          <option value="other">✍️ Інше</option>
        </select>
        <button class="submit" id="feedback-btn" type="submit">Надіслати</button>
      </div>
      <textarea id="feedback-text" name="text" rows="4" maxlength="2000"
                placeholder="Напишіть ідею або опишіть, що працює неправильно..."></textarea>
      <div class="form-status" id="feedback-status" role="status"></div>
      <p class="form-note">Повідомлення надійде адміністратору через бота.</p>
    </form>
    """

    if active_tab == "feedback":
        active_form = feedback_form
    elif active_tab == "sales":
        active_form = sales_form
    else:
        active_form = search_form

    if active_tab == "feedback":
        tip_text = (
            "<b>Маєте пропозицію?</b> Розкажіть про ідею або повідомте про помилку — "
            "ми обов'язково розглянемо ваш відгук."
        )
    elif active_tab == "sales":
        tip_text = (
            "<b>Акції:</b> показано товари зі знижкою у вибраних магазинах. "
            "Налаштуйте категорію та сортування вище."
        )
    else:
        tip_text = (
            "<b>Як читати:</b> «на вагу» — ціна за 1 кг, «упаковка» — фасований товар. "
            "Типове сортування — ціна за кг/л."
        )

    def tab_url(tab: str) -> str:
        if tab == "feedback":
            return "/?tab=feedback"
        params: dict[str, object] = {
            "tab": tab,
            "sort": sort_by,
            "limit": limit,
            "stores": selected_stores,
        }
        if tab == "search":
            params["q"] = query
            params["sale"] = sale_filter
        else:
            params["category"] = category
        return "/?" + urlencode(params, doseq=True)

    tabs_html = f"""
    <nav class="tabs" aria-label="Розділи">
      <a class="tab {'active' if active_tab == 'search' else ''}" href="{esc(tab_url('search'))}">
        <span class="tab-ico">🔍</span><span>Пошук</span>
      </a>
      <a class="tab {'active' if active_tab == 'sales' else ''}" href="{esc(tab_url('sales'))}">
        <span class="tab-ico">🏷️</span><span>Акції</span>
      </a>
      <a class="tab {'active' if active_tab == 'feedback' else ''}" href="{esc(tab_url('feedback'))}">
        <span class="tab-ico">💬</span><span>Відгук</span>
      </a>
    </nav>
    """

    return f"""<!doctype html>
<html lang="uk">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no">
<meta name="color-scheme" content="dark light">
<title>Де дешевше — Mini App</title>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<style>
  :root {{
    --bg: #0b1020;
    --surface: rgba(255,255,255,.055);
    --surface-2: rgba(255,255,255,.085);
    --border: rgba(255,255,255,.10);
    --text: #eef2ff;
    --muted: #94a0bd;
    --green: #34d399;
    --accent: #7c5cff;
    --shadow: 0 18px 50px rgba(0,0,0,.35);
    --radius: 18px;
  }}
  body[data-theme="light"] {{
    --bg: #f4f6fb;
    --surface: #fff;
    --surface-2: #f4f7fc;
    --border: #e1e7f0;
    --text: #17213a;
    --muted: #6c7891;
    --green: #059669;
    --shadow: 0 12px 32px rgba(23,33,58,.08);
  }}
  * {{ box-sizing: border-box; }}
  html, body {{ min-height: 100%; }}
  body {{
    margin: 0; color: var(--text);
    background:
      radial-gradient(1100px 500px at 8% -8%, rgba(124,92,255,.22), transparent 60%),
      radial-gradient(900px 480px at 100% 0%, rgba(16,185,129,.18), transparent 55%),
      var(--bg);
    font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif;
    font-size: 15px; line-height: 1.45;
    padding-bottom: calc(76px + env(safe-area-inset-bottom));
    transition: background .25s, color .25s;
  }}
  a {{ color: inherit; }}
  .shell {{ max-width: 1180px; margin: 0 auto; padding: 0 18px; }}
  header {{ padding: 18px 0 10px; }}
  .head-row {{ display: flex; align-items: center; justify-content: space-between; gap: 12px; }}
  .brand {{ display: flex; align-items: center; gap: 12px; }}
  .logo {{
    width: 42px; height: 42px; border-radius: 14px; display: grid; place-items: center;
    background: linear-gradient(135deg, var(--green), var(--accent));
    box-shadow: 0 8px 24px rgba(52,211,153,.25); font-size: 20px;
  }}
  h1 {{ margin: 0; font-size: 22px; letter-spacing: -.02em; }}
  .sub {{ margin: 2px 0 0; color: var(--muted); font-size: 12px; }}
  .theme {{
    width: 40px; height: 40px; border-radius: 12px; border: 1px solid var(--border);
    background: var(--surface); color: var(--text); font-size: 18px; cursor: pointer;
    display: grid; place-items: center;
  }}
  .search {{
    margin-top: 14px; padding: 14px; border: 1px solid var(--border);
    border-radius: var(--radius); background: var(--surface);
    backdrop-filter: blur(18px); box-shadow: var(--shadow);
  }}
  .line {{ display: flex; flex-wrap: wrap; gap: 10px; }}
  .input-wrap {{ position: relative; flex: 1 1 280px; }}
  .input-wrap .ico {{ position: absolute; left: 14px; top: 50%; transform: translateY(-50%); color: var(--muted); }}
  input[type=text] {{
    width: 100%; padding: 13px 14px 13px 40px; border-radius: 13px;
    border: 1px solid var(--border); background: var(--surface-2);
    color: var(--text); font-size: 16px; outline: none;
  }}
  input[type=text]:focus {{ border-color: var(--green); box-shadow: 0 0 0 3px rgba(52,211,153,.16); }}
  input[type=text]::placeholder {{ color: var(--muted); }}
  select {{
    padding: 13px 14px; border-radius: 13px; border: 1px solid var(--border);
    background: var(--surface-2); color: var(--text); font-size: 14px;
    outline: none; cursor: pointer; min-width: 150px;
  }}
  .submit {{
    border: 0; border-radius: 13px; padding: 13px 22px; font-weight: 700; font-size: 15px;
    color: #061a14; cursor: pointer; white-space: nowrap;
    background: linear-gradient(135deg, #6ee7b7, #34d399);
    box-shadow: 0 8px 24px rgba(52,211,153,.25);
  }}
  .chips {{ display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }}
  .chip {{ position: relative; cursor: pointer; }}
  .chip input {{ position: absolute; opacity: 0; pointer-events: none; }}
  .chip span {{
    display: inline-block; padding: 7px 13px; border-radius: 999px;
    border: 1px solid var(--border); background: var(--surface-2);
    color: var(--muted); font-size: 13px;
  }}
  .chip input:checked + span {{
    color: #061a14; border-color: transparent; font-weight: 700;
    background: linear-gradient(135deg, #6ee7b7, #34d399);
  }}
  .tip {{
    margin: 14px 0; padding: 11px 14px; border-radius: 14px;
    border: 1px solid rgba(52,211,153,.2); background: rgba(52,211,153,.07);
    color: var(--muted); font-size: 12.5px;
  }}
  .tip b {{ color: var(--text); }}
  textarea {{
    width: 100%; margin-top: 10px; padding: 13px 14px; border-radius: 13px;
    border: 1px solid var(--border); background: var(--surface-2);
    color: var(--text); font: inherit; font-size: 15px; resize: vertical;
    outline: none; min-height: 96px;
  }}
  textarea:focus {{ border-color: var(--green); box-shadow: 0 0 0 3px rgba(52,211,153,.16); }}
  textarea::placeholder {{ color: var(--muted); }}
  .form-status {{ margin-top: 8px; min-height: 18px; font-size: 13px; color: var(--green); }}
  .form-note {{ margin: 2px 0 0; color: var(--muted); font-size: 12px; }}
  .summary {{ margin: 4px 2px 12px; color: var(--muted); font-size: 13px; }}
  .summary b {{ color: var(--green); }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 12px; }}
  .card {{
    display: flex; gap: 12px; padding: 12px; min-height: 140px;
    border: 1px solid var(--border); border-radius: var(--radius);
    background: var(--surface);
  }}
  .card[hidden] {{ display: none; }}
  .thumb {{
    flex: 0 0 96px; height: 116px; border-radius: 12px; overflow: hidden;
    background: #ffffff; border: 1px solid rgba(23,33,58,.08);
    padding: 6px; display: flex; align-items: center; justify-content: center;
    box-shadow: inset 0 0 0 1px rgba(255,255,255,.6);
  }}
  .thumb img {{
    max-width: 100%; max-height: 100%; width: auto; height: auto;
    object-fit: contain; display: block; border-radius: 6px;
    transition: transform .2s ease;
  }}
  .card:hover .thumb img {{ transform: scale(1.05); }}
  .img-fallback {{
    display: none; align-items: center; justify-content: center;
    width: 100%; height: 100%; font-size: 28px; color: #475569; opacity: .75;
  }}
  .body {{ flex: 1; min-width: 0; display: flex; flex-direction: column; }}
  .tags {{ display: flex; flex-wrap: wrap; gap: 5px; align-items: center; }}
  .store {{ font-weight: 800; color: var(--green); font-size: 13px; }}
  .tag {{ font-size: 10.5px; font-weight: 700; padding: 2px 8px; border-radius: 999px; }}
  .tag-weight {{ background: rgba(251,191,36,.16); color: #fbbf24; }}
  .tag-piece {{ background: rgba(124,92,255,.18); color: #a78bfa; }}
  .tag-packed {{ background: var(--surface-2); color: var(--muted); }}
  .tag-action {{ background: rgba(248,113,113,.16); color: #fca5a5; }}
  .hint {{ font-size: 10.5px; padding: 2px 8px; border-radius: 999px; border: 1px solid var(--border); color: var(--muted); }}
  .hint-fresh {{ color: var(--green); border-color: rgba(52,211,153,.3); }}
  .hint-dried {{ color: #f472b6; border-color: rgba(244,114,182,.3); }}
  .hint-salted {{ color: #fb923c; border-color: rgba(251,146,60,.3); }}
  .hint-marinated {{ color: #facc15; border-color: rgba(250,204,21,.3); }}
  .name {{
    margin: 7px 0 5px; font-size: 14px; font-weight: 600; line-height: 1.35;
    display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
  }}
  .meta {{ display: flex; flex-wrap: wrap; gap: 4px 10px; font-size: 11.5px; color: var(--muted); }}
  .unit {{ color: var(--green); font-weight: 700; }}
  .unit-muted {{ color: var(--muted); }}
  .bottom {{
    margin-top: auto; padding-top: 8px; display: flex; align-items: flex-end;
    justify-content: space-between; gap: 8px;
  }}
  .prices {{ display: flex; flex-wrap: wrap; gap: 4px 8px; align-items: baseline; }}
  .price {{ font-size: 20px; font-weight: 800; }}
  .price small {{ font-size: 11px; font-weight: 600; color: var(--muted); }}
  .old {{ font-size: 12px; color: var(--muted); text-decoration: line-through; }}
  .discount {{
    font-size: 11px; font-weight: 800; padding: 2px 7px; border-radius: 999px;
    background: rgba(248,113,113,.16); color: #fca5a5;
  }}
  .go {{
    flex: 0 0 auto; width: 34px; height: 34px; border-radius: 10px;
    display: grid; place-items: center; text-decoration: none;
    background: rgba(52,211,153,.14); color: var(--green); font-size: 17px; font-weight: 700;
  }}
  .go-muted {{ opacity: .4; }}
  .more {{
    display: block; margin: 20px auto 0; padding: 12px 28px; border-radius: 999px;
    border: 1px solid var(--border); background: var(--surface);
    color: var(--text); font-weight: 600; cursor: pointer;
  }}
  .empty {{
    grid-column: 1/-1; text-align: center; padding: 48px 18px;
    border: 1px dashed var(--border); border-radius: var(--radius); color: var(--muted);
  }}
  .empty-ico {{ font-size: 36px; }}
  .empty h3 {{ color: var(--text); margin: 8px 0 4px; }}
  .alert {{
    margin: 0 0 14px; padding: 12px 14px; border-radius: 14px; font-size: 13px;
    background: rgba(248,113,113,.09); border: 1px solid rgba(248,113,113,.25); color: #fca5a5;
  }}
  .alert ul {{ margin: 6px 0 0; padding-left: 18px; }}
  .tabs {{
    position: fixed; left: 0; right: 0; bottom: 0; z-index: 20;
    display: flex; gap: 8px; padding: 8px 18px calc(10px + env(safe-area-inset-bottom));
    border-top: 1px solid var(--border);
    background: rgba(11,16,32,.92); backdrop-filter: blur(18px);
  }}
  body[data-theme="light"] .tabs {{ background: rgba(255,255,255,.94); }}
  .tab {{
    flex: 1; display: flex; flex-direction: column; align-items: center; gap: 2px;
    padding: 7px 0; border-radius: 14px; text-decoration: none;
    color: var(--muted); font-size: 12px; font-weight: 700;
    border: 1px solid transparent;
  }}
  .tab-ico {{ font-size: 19px; line-height: 1; }}
  .tab.active {{
    color: #061a14; border-color: transparent;
    background: linear-gradient(135deg, #6ee7b7, #34d399);
  }}
  footer {{ text-align: center; color: var(--muted); font-size: 12px; padding: 24px 16px 20px; }}
  @media (max-width: 640px) {{
    .grid {{ grid-template-columns: 1fr; }}
    select {{ width: 100%; }}
    .submit {{ width: 100%; padding: 14px; }}
  }}
</style>
</head>
<body>
<div class="shell">
  <header>
    <div class="head-row">
      <div class="brand">
        <div class="logo">🛒</div>
        <div>
          <h1>Де дешевше?</h1>
          <p class="sub">Сільпо · АТБ · Варус · Новус · Траш · Велмарт</p>
        </div>
      </div>
      <button class="theme" id="theme" type="button" title="Змінити тему">☀️</button>
    </div>

    {active_form}
  </header>

  <div class="tip">{tip_text}</div>

  {summary}
  {errors_html}
  <div class="grid" id="grid">{cards}</div>
  {more_button}
</div>

<footer>Ціни залежать від міста та магазину · Telegram Mini App</footer>
{tabs_html}

<script>
(function () {{
  var tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
  if (tg) {{
    tg.ready();
    tg.expand();
  }}

  var btn = document.getElementById('theme');
  var defaultTheme = (tg && tg.colorScheme === 'light') ? 'light' : 'dark';
  var saved = localStorage.getItem('gde-theme') || defaultTheme;
  document.body.dataset.theme = saved;
  btn.textContent = saved === 'dark' ? '☀️' : '🌙';
  btn.addEventListener('click', function () {{
    var next = document.body.dataset.theme === 'dark' ? 'light' : 'dark';
    document.body.dataset.theme = next;
    localStorage.setItem('gde-theme', next);
    btn.textContent = next === 'dark' ? '☀️' : '🌙';
  }});

  document.querySelectorAll('a[data-ext-url]').forEach(function (a) {{
    a.addEventListener('click', function (e) {{
      var href = a.getAttribute('data-ext-url');
      if (tg && typeof tg.openLink === 'function' && href) {{
        e.preventDefault();
        tg.openLink(href);
      }}
    }});
  }});

  document.querySelectorAll('form.search:not(#feedback-form)').forEach(function (form) {{
    form.addEventListener('submit', function () {{
      var btn = form.querySelector('button[type=submit]');
      if (btn) {{
        btn.textContent = 'Шукаємо...';
        btn.disabled = true;
      }}
    }});
  }});

  var feedbackForm = document.getElementById('feedback-form');
  if (feedbackForm) {{
    feedbackForm.addEventListener('submit', function (e) {{
      e.preventDefault();
      var text = document.getElementById('feedback-text').value.trim();
      var category = document.getElementById('feedback-category').value;
      var status = document.getElementById('feedback-status');
      var btn = document.getElementById('feedback-btn');
      if (text.length < 3) {{
        status.textContent = 'Напишіть хоча б кілька символів.';
        return;
      }}
      if (!tg || typeof tg.sendData !== 'function') {{
        status.textContent = 'Відкрийте Mini App через бота, щоб надіслати відгук.';
        return;
      }}
      try {{
        tg.sendData(JSON.stringify({{
          type: 'feedback',
          category: category,
          text: text.slice(0, 2000)
        }}));
        status.textContent = 'Надсилаємо...';
        if (btn) btn.disabled = true;
      }} catch (err) {{
        status.textContent = 'Не вдалося надіслати. Спробуйте кнопку «Відгук» у боті.';
      }}
    }});
  }}

  var all = Array.prototype.slice.call(document.querySelectorAll('.card'));
  var shown = {PAGE_SIZE};
  all.forEach(function (card, i) {{ card.hidden = i >= shown; }});
  var more = document.getElementById('more');
  if (more) {{
    more.addEventListener('click', function () {{
      shown += {PAGE_SIZE};
      all.forEach(function (card, i) {{ card.hidden = i >= shown; }});
      if (shown >= all.length) more.remove();
    }});
  }}
}})();
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def do_HEAD(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/favicon.ico":
            self.send_response(204)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.send_header("Allow", "GET, HEAD, OPTIONS")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return

        params = parse_qs(parsed.query)
        query = (params.get("q") or [""])[0].strip()
        active_tab = (params.get("tab") or ["search"])[0]
        if active_tab not in ("search", "sales", "feedback"):
            active_tab = "search"
        category = (params.get("category") or ["dairy"])[0]
        if category not in SALE_CATEGORIES:
            category = "dairy"
        sale_filter = (params.get("sale") or ["all"])[0]
        sort_default = "discount" if active_tab == "sales" else "unit"
        sort_by = (params.get("sort") or [sort_default])[0]
        try:
            limit = int((params.get("limit") or ["20"])[0])
        except ValueError:
            limit = 20
        limit = max(5, min(limit, 50))

        raw_stores = params.get("stores")
        if raw_stores is None:
            selected_stores = list(STORES)
        else:
            selected_stores = [
                key for key in (v.strip().lower() for v in raw_stores)
                if key in STORES
            ]
            if not selected_stores:
                selected_stores = list(STORES)

        products: list[dict] = []
        errors: list[tuple[str, str]] = []
        if parsed.path == "/api/search":
            found, errors = find_products(query or "продукт", selected_stores, limit)
            products = [p.to_dict() for p in found]
            payload = json.dumps(
                {"query": query, "products": products, "errors": errors},
                ensure_ascii=False,
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)
            return

        if parsed.path == "/api/sales":
            found, errors = search_sales(category, selected_stores, limit)
            payload = json.dumps(
                {"category": category, "products": [p.to_dict() for p in found],
                 "errors": errors},
                ensure_ascii=False,
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)
            return

        searched = active_tab == "sales" or (active_tab == "search" and bool(query))
        if searched:
            if active_tab == "sales":
                found, errors = search_sales(category, selected_stores, limit)
            else:
                found, errors = find_products(query, selected_stores, limit)
            products = [p.to_dict() for p in found]

        html = render_page(
            query=query,
            products=products,
            errors=errors,
            selected_stores=selected_stores,
            sale_filter=sale_filter,
            sort_by=sort_by,
            searched=searched,
            limit=limit,
            active_tab=active_tab,
            category=category,
        )
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:
        pass


def main() -> None:
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(encoding="utf-8")
            except Exception:
                pass

    parser = argparse.ArgumentParser(description="Сервер Mini App пошуку продуктів")
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--host", default=HOST)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    raw_port = os.environ.get("PORT")
    port = int(raw_port) if raw_port and raw_port.isdigit() else args.port
    on_render = bool(raw_port)

    server = ThreadingHTTPServer((args.host, port), Handler)
    url = f"http://localhost:{port}"
    print(f"Сервер запущено: {url}", flush=True)
    ip = local_ip()
    if ip and not on_render:
        print(f"Локальна IP-адреса: http://{ip}:{port}", flush=True)
    if not args.no_browser and not on_render:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nЗупинено.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()