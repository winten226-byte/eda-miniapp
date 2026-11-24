# -*- coding: utf-8 -*-
"""
Telegram-бот Mini App «Где дешевле?» — все обработчики в одном файле.

Переменные окружения:
    BOT_TOKEN   — токен от @BotFather (или файл bot_token.txt)
    WEBAPP_URL  — HTTPS-ссылка на Mini App (иначе берётся из tunnel_url.txt)
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    WebAppInfo,
)

HERE = Path(__file__).resolve().parent
URL_FILE = HERE / "tunnel_url.txt"
TOKEN_FILE = HERE / "bot_token.txt"  # не коммитится в git

# Картинка для /start. Можно заменить файл или задать ссылку.
START_IMAGE_URL = ""
START_IMAGE = HERE / "assets" / "start.png"

START_CAPTION = (
    "🛒 Знаходь найдешевші продукти у Сільпо, АТБ, Варус, "
    "Новус, Траш і Велмарт.\n\n"
    "Натисни «Go», щоб відкрити Mini App 👇"
)
INFO_TEXT = (
    "ℹ️ Інформація\n\n"
    "Бот порівнює ціни в українських супермаркетах.\n"
    "У Mini App дві вкладки знизу:\n"
    "🔍 Поиск — пошук товару за назвою;\n"
    "🏷️ Акції — товари зі знижкою, з налаштуванням категорії "
    "і магазинів.\n\n"
    "Є сортування за ціною, ціною за кг/л і розміром знижки."
)


# ---------- Вспомогательные функции ----------

def get_token() -> str:
    """Сначала env (Render), потом локальный bot_token.txt."""
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


def build_inline_kb(url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Go", web_app=WebAppInfo(url=url))]
        ]
    )


def build_reply_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Інформація")]],
        resize_keyboard=True,
    )


# ---------- Обработчики ----------

async def cmd_start(message: Message) -> None:
    url = get_webapp_url()

    # Реплай-кнопку показываем первым сообщением — она появится в любом случае
    await message.answer(
        "Користуйтеся кнопкою нижче, щоб дізнатися більше 👇",
        reply_markup=build_reply_kb(),
    )

    if not url:
        await message.answer(
            "HTTPS-ссылка для Mini App пока не найдена.\n"
            "Запустите <code>python tunnel.py</code> или отправьте:\n"
            "<code>/seturl https://ваш-домен.lhr.life</code>",
            parse_mode="HTML",
        )
        return

    photo: object = START_IMAGE_URL or (
        FSInputFile(START_IMAGE) if START_IMAGE.exists() else None
    )
    try:
        if photo:
            await message.answer_photo(
                photo=photo,  # type: ignore[arg-type]
                caption=START_CAPTION,
                reply_markup=build_inline_kb(url),
            )
        else:
            await message.answer(
                START_CAPTION,
                reply_markup=build_inline_kb(url),
            )
    except Exception as exc:  # noqa: BLE001 — кнопка Go должна остаться
        print(f"[start] Не удалось отправить фото: {exc}", flush=True)
        await message.answer(
            START_CAPTION + "\n\n(фото тимчасово недоступне)",
            reply_markup=build_inline_kb(url),
        )


async def cmd_info(message: Message) -> None:
    await message.answer(INFO_TEXT, reply_markup=build_reply_kb())


async def cmd_app(message: Message) -> None:
    url = get_webapp_url()
    if not url:
        await message.answer(
            "Ссылка ещё не задана. Используйте /seturl <https://...>",
            reply_markup=build_reply_kb(),
        )
        return
    await message.answer(
        "Запускай Mini App:",
        reply_markup=build_inline_kb(url),
    )


async def cmd_seturl(message: Message) -> None:
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip().startswith("https://"):
        await message.answer(
            "Укажите корректную HTTPS-ссылку:\n"
            "<code>/seturl https://example.lhr.life</code>",
            parse_mode="HTML",
            reply_markup=build_reply_kb(),
        )
        return
    new_url = parts[1].strip().rstrip("/")
    save_webapp_url(new_url)
    await message.answer(
        f"Ссылка обновлена: <code>{new_url}</code>",
        parse_mode="HTML",
        reply_markup=build_inline_kb(new_url),
    )


async def fallback_text(message: Message) -> None:
    url = get_webapp_url()
    if url:
        await message.answer(
            "Нажмите «Go», чтобы открыть Mini App:",
            reply_markup=build_inline_kb(url),
        )
    else:
        await message.answer(
            "Введите /start или задайте ссылку через /seturl.",
            reply_markup=build_reply_kb(),
        )


def register_handlers(dp: Dispatcher) -> None:
    dp.message.register(cmd_start, CommandStart())
    dp.message.register(cmd_info, Command("info"))
    dp.message.register(cmd_info, F.text == "Інформація")
    dp.message.register(cmd_app, Command("app"))
    dp.message.register(cmd_seturl, Command("seturl"))
    dp.message.register(fallback_text, F.text)


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
            "Не найден токен бота. Варианты:\n"
            "  1) задайте переменную окружения BOT_TOKEN;\n"
            "  2) создайте файл bot_token.txt в папке проекта и впишите туда токен.\n"
            "Файл bot_token.txt добавлен в .gitignore."
        )

    bot = Bot(token=token)
    dp = Dispatcher()
    register_handlers(dp)

    print("Бот запущен", flush=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
