# -*- coding: utf-8 -*-
"""
Telegram-бот для запуска Mini App поиска самых дешёвых продуктов.

Переменные окружения:
    BOT_TOKEN   — токен бота от @BotFather (обязательно)
    WEBAPP_URL  — HTTPS-ссылка (опционально; если не задана, берётся из tunnel_url.txt)
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    WebAppInfo,
)

HERE = Path(__file__).resolve().parent
URL_FILE = HERE / "tunnel_url.txt"
DEFAULT_BOT_TOKEN = "9283096243:AAE44cCoTSSBKIiXvsFpiSpiQW6yXqrFRyw"


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


def build_inline_kb(url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🛒 Открыть Mini App",
                    web_app=WebAppInfo(url=url),
                )
            ]
        ]
    )


def build_reply_kb(url: str) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text="🛒 Поиск продуктов",
                    web_app=WebAppInfo(url=url),
                )
            ]
        ],
        resize_keyboard=True,
    )


async def main() -> None:
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(encoding="utf-8")
            except Exception:
                pass

    token = (os.getenv("BOT_TOKEN") or DEFAULT_BOT_TOKEN).strip()
    if not token:
        raise RuntimeError(
            "Не задан BOT_TOKEN. Укажите переменную окружения BOT_TOKEN "
            "или впишите токен в DEFAULT_BOT_TOKEN в bot.py."
        )

    bot = Bot(token=token)
    dp = Dispatcher()

    @dp.message(CommandStart())
    async def cmd_start(message: Message) -> None:
        url = get_webapp_url()
        if not url:
            await message.answer(
                "HTTPS-ссылка для Mini App пока не найдена.\n"
                "Запустите <code>python tunnel.py</code> или отправьте ссылку командой:\n"
                "<code>/seturl https://ваш-домен.lhr.life</code>",
                parse_mode="HTML",
            )
            return

        await message.answer(
            "Привет! Открой Mini App, чтобы сравнить цены на продукты в "
            "Сільпо, АТБ, Варус, Новус, Траш и Велмарт:",
            reply_markup=build_reply_kb(url),
        )
        await message.answer(
            f"Актуальный адрес: <code>{url}</code>",
            parse_mode="HTML",
            reply_markup=build_inline_kb(url),
        )

    @dp.message(Command("app"))
    async def cmd_app(message: Message) -> None:
        url = get_webapp_url()
        if not url:
            await message.answer("Ссылка ещё не задана. Используйте /seturl <https://...>")
            return
        await message.answer(
            "Запускай Mini App:",
            reply_markup=build_inline_kb(url),
        )

    @dp.message(Command("seturl"))
    async def cmd_seturl(message: Message) -> None:
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip().startswith("https://"):
            await message.answer(
                "Укажите корректную HTTPS-ссылку:\n"
                "<code>/seturl https://example.lhr.life</code>",
                parse_mode="HTML",
            )
            return
        new_url = parts[1].strip().rstrip("/")
        save_webapp_url(new_url)
        await message.answer(
            f"Ссылка обновлена: <code>{new_url}</code>",
            parse_mode="HTML",
            reply_markup=build_inline_kb(new_url),
        )

    @dp.message(F.text)
    async def fallback_text(message: Message) -> None:
        url = get_webapp_url()
        if url:
            await message.answer(
                "Нажмите кнопку ниже, чтобы открыть поиск в Mini App:",
                reply_markup=build_inline_kb(url),
            )
        else:
            await message.answer("Введите /start или задайте ссылку через /seturl.")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())