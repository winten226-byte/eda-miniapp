# -*- coding: utf-8 -*-
"""
Обработчики Telegram-бота.

Сюда добавляйте новые команды и сценарии: /help, рассылки, Answers,
обработку кнопок и т.д. Запуск и токен остаются в bot.py.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from aiogram import Dispatcher, F
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

# Если хотите картинку по ссылке — вставьте URL сюда.
# Иначе используется локальный файл assets/start.png.
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


def build_inline_kb(url: str) -> InlineKeyboardMarkup:
    """Инлайн-кнопка под сообщением с фото."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Go", web_app=WebAppInfo(url=url))]
        ]
    )


def build_reply_kb() -> ReplyKeyboardMarkup:
    """Реплай-кнопка «Інформація» под сообщением."""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Інформація")]],
        resize_keyboard=True,
    )


def register_handlers(
    dp: Dispatcher,
    *,
    get_url: Callable[[], str],
    save_url: Callable[[str], None],
) -> None:
    """Регистрирует все обработчики. Вызывается из bot.py."""

    @dp.message(CommandStart())
    async def cmd_start(message: Message) -> None:
        url = get_url()
        if not url:
            await message.answer(
                "HTTPS-ссылка для Mini App пока не найдена.\n"
                "Запустите <code>python tunnel.py</code> или отправьте:\n"
                "<code>/seturl https://ваш-домен.lhr.life</code>",
                parse_mode="HTML",
                reply_markup=build_reply_kb(),
            )
            return

        photo: object = START_IMAGE_URL or (
            FSInputFile(START_IMAGE) if START_IMAGE.exists() else None
        )
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

        # Реплай-клавиатура отдельным сообщением (у фото — инлайн-кнопка)
        await message.answer(
            "Користуйтеся кнопкою нижче, щоб дізнатися більше 👇",
            reply_markup=build_reply_kb(),
        )

    @dp.message(Command("info"))
    @dp.message(F.text == "Інформація")
    async def cmd_info(message: Message) -> None:
        await message.answer(INFO_TEXT, reply_markup=build_reply_kb())

    @dp.message(Command("app"))
    async def cmd_app(message: Message) -> None:
        url = get_url()
        if not url:
            await message.answer(
                "Ссылка ещё не задана. Используйте /seturl <https://...>"
            )
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
        save_url(new_url)
        await message.answer(
            f"Ссылка обновлена: <code>{new_url}</code>",
            parse_mode="HTML",
            reply_markup=build_inline_kb(new_url),
        )

    @dp.message(F.text)
    async def fallback_text(message: Message) -> None:
        url = get_url()
        if url:
            await message.answer(
                "Нажмите «Go», чтобы открыть Mini App:",
                reply_markup=build_inline_kb(url),
            )
        else:
            await message.answer("Введите /start или задайте ссылку через /seturl.")
