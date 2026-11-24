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
TOKEN_FILE = HERE / "bot_token.txt"  # локально, не коммитится в git


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


def get_token() -> str:
    """Сначала env (Render), потом локальный bot_token.txt (не в git)."""
    token = (os.getenv("BOT_TOKEN") or "").strip()
    if token:
        return token
    if TOKEN_FILE.exists():
        return TOKEN_FILE.read_text(encoding="utf-8-sig").strip()
    return ""


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
            "Не найден токен бота. Варианты:\n"
            "  1) задайте переменную окружения BOT_TOKEN;\n"
            "  2) создайте файл bot_token.txt в папке проекта и впишите туда токен.\n"
            "Файл bot_token.txt добавлен в .gitignore."
        )

    bot = Bot(token=token)
    dp = Dispatcher()
    register_handlers(dp, get_url=get_webapp_url, save_url=save_webapp_url)

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
