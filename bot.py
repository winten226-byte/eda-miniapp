# -*- coding: utf-8 -*-
"""Запуск Telegram-бота Mini App «Де дешевше?».

Обробники команд і відгуків знаходяться у ``bot_handlers.py``.

Змінні оточення:
    BOT_TOKEN   — токен від @BotFather (або файл bot_token.txt)
    WEBAPP_URL  — HTTPS-посилання на Mini App (інакше береться з tunnel_url.txt)
    ADMIN_IDS   — Telegram ID адміністраторів через кому (для /stats і відгуків)
    STATS_FILE  — шлях до файлу статистики (за замовчуванням stats.json)
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

from aiogram import Bot, Dispatcher

from bot_handlers import register_handlers
from stats_store import StatsStore, parse_admin_ids

HERE = Path(__file__).resolve().parent
URL_FILE = HERE / "tunnel_url.txt"
TOKEN_FILE = HERE / "bot_token.txt"  # не комітиться в git

logging.basicConfig(level=logging.INFO)


# ---------- Допоміжні функції ----------

def get_token() -> str:
    """Спочатку змінна оточення (Render), потім локальний bot_token.txt."""
    token = (os.getenv("BOT_TOKEN") or "").strip()
    if token:
        return token
    if TOKEN_FILE.exists():
        return TOKEN_FILE.read_text(encoding="utf-8-sig").strip()
    return ""


def get_webapp_url() -> str:
    env_url = (os.getenv("WEBAPP_URL") or "").strip()
    if env_url.startswith("https://"):
        return env_url.rstrip("/")
    if URL_FILE.exists():
        content = URL_FILE.read_text(encoding="utf-8-sig").strip()
        if content.startswith("https://"):
            return content.rstrip("/")
    return ""


def save_webapp_url(url: str) -> None:
    URL_FILE.write_text(url.strip().rstrip("/") + "\n", encoding="utf-8")


# ---------- Запуск ----------

async def main() -> None:
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(encoding="utf-8")
            except Exception:
                pass

    token = get_token()
    if not token:
        raise RuntimeError(
            "Не знайдено токен бота. Варіанти:\n"
            "  1) задайте змінну оточення BOT_TOKEN;\n"
            "  2) створіть файл bot_token.txt у папці проєкту з токеном.\n"
            "Файл bot_token.txt додано до .gitignore."
        )

    stats = StatsStore(admin_ids=parse_admin_ids())
    bot = Bot(token=token)
    dp = Dispatcher()
    register_handlers(
        dp,
        get_url=get_webapp_url,
        save_url=save_webapp_url,
        stats=stats,
        bot=bot,
    )

    admins = ", ".join(str(item) for item in sorted(stats.admin_ids)) or "не налаштовано"
    print(f"Бот запущено. Адміністратори: {admins}", flush=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
