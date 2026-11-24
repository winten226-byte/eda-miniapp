# -*- coding: utf-8 -*-
"""
Точка входа Telegram-бота для запуска Mini App.

Все обработчики команд вынесены в bot_handlers.py — добавляйте новую
логику бота туда, не трогая запуск и токен.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from aiogram import Bot, Dispatcher

from bot_handlers import register_handlers

HERE = Path(__file__).resolve().parent
URL_FILE = HERE / "tunnel_url.txt"


def get_webapp_url() -> str:
    env_url = (os.getenv("WEBAPP_URL") or "").strip()
    if env_url.startswith("https://"):
        return env_url.rstrip("/")
    if URL_FILE.exists():
        content = URL_FILE.read_text(encoding="utf-8").strip()
        if content.startswith("https://"):
            return content.rstrip("/")
    return ""


def save_webapp_url(url: str) -> None:
    URL_FILE.write_text(url.strip().rstrip("/") + "\n", encoding="utf-8")


async def main() -> None:
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(encoding="utf-8")
            except Exception:
                pass

    token = (os.getenv("BOT_TOKEN") or "").strip()
    if not token:
        raise RuntimeError(
            "Не задан BOT_TOKEN. Укажите переменную окружения BOT_TOKEN "
            "(на Render — в Environment сервиса бота, локально — перед запуском)."
        )

    bot = Bot(token=token)
    dp = Dispatcher()
    register_handlers(dp, get_url=get_webapp_url, save_url=save_webapp_url)

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
