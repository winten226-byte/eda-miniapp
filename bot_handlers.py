# -*- coding: utf-8 -*-
"""Обробники Telegram-бота: команди, відгуки та адмінська статистика.

Запуск і читання токена знаходяться у ``bot.py``.
"""
from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Callable

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    User,
    WebAppInfo,
)

from stats_store import StatsStore

HERE = Path(__file__).resolve().parent

# Якщо потрібна картинка за посиланням — вставте URL сюди.
# Інакше використовується локальний файл assets/start.png.
START_IMAGE_URL = ""
START_IMAGE = HERE / "assets" / "start.png"

FEEDBACK_CATEGORIES = {
    "idea": "💡 Ідея",
    "bug": "🐛 Несправність",
    "other": "✍️ Інше",
}

START_CAPTION = (
    "🛒 Знаходьте найвигідніші продукти у Сільпо, АТБ, Варус, "
    "Новус, Траш і Велмарт.\n\n"
    "Натисни «Відкрити», щоб запустити Mini App 👇"
)
INFO_TEXT = (
    "ℹ️ Інформація\n\n"
    "Бот порівнює ціни в українських супермаркетах.\n"
    "У Mini App три вкладки знизу:\n"
    "🔍 Пошук — пошук товару за назвою;\n"
    "🏷️ Акції — товари зі знижкою, з вибором категорії "
    "і магазинів;\n"
    "💬 Відгук — надішліть ідею або повідомте про несправність.\n\n"
    "Є сортування за ціною, ціною за кг/л та розміром знижки."
)


class Feedback(StatesGroup):
    """Стан очікування тексту відгуку."""

    text = State()


def build_inline_kb(url: str) -> InlineKeyboardMarkup:
    """Інлайн-кнопки під повідомленням із картинкою."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Відкрити", web_app=WebAppInfo(url=url))],
            [InlineKeyboardButton(text="💬 Залишити відгук", callback_data="feedback")],
        ]
    )


def build_reply_kb(url: str = "") -> ReplyKeyboardMarkup:
    """Постійні кнопки під полем вводу."""
    rows: list[list[KeyboardButton]] = [
        [KeyboardButton(text="ℹ️ Інформація"), KeyboardButton(text="💬 Відгук")]
    ]
    if url:
        rows.append([KeyboardButton(text="🚀 Відкрити", web_app=WebAppInfo(url=url))])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def build_cancel_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ Скасувати")]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def user_label(user: User) -> str:
    username = getattr(user, "username", None)
    name = getattr(user, "full_name", None) or getattr(user, "first_name", None) or "Невідомий"
    return f"{name} (@{username})" if username else str(name)


def format_summary(summary: dict) -> str:
    """Форматувати статистику для адмінської команди /stats."""
    lines = [
        "📊 <b>Статистика бота</b>",
        "",
        f"👥 Користувачів всього: <b>{summary['total_users']}</b>",
        f"🆕 Нових за 24 год: <b>{summary['new_24h']}</b> · за 7 днів: <b>{summary['new_7d']}</b>",
        f"🟢 Активних за 24 год: <b>{summary['active_24h']}</b>",
        "",
        f"▶️ Запусків /start: <b>{summary['starts']}</b>",
        f"🚀 Відкриттів Mini App: <b>{summary['opens']}</b>",
        f"💬 Відгуків: <b>{summary['saved_feedbacks']}</b>",
    ]
    recent = summary["recent_users"]
    if recent:
        lines += ["", "Останні користувачі:"]
        for _uid, record in recent:
            name = html.escape(record.get("name") or "Невідомий")
            username = record.get("username") or ""
            who = f"{name} (@{html.escape(username)})" if username else name
            lines.append(
                f"• {who} — {record.get('events', 0)} дій, "
                f"останній раз {html.escape(str(record.get('last_seen', ''))[:16])}"
            )
    lines += [
        "",
        "Дані зберігаються у <code>stats.json</code>.",
        "Останні відгуки — командою /feedbacks.",
    ]
    return "\n".join(lines)


def register_handlers(
    dp: Dispatcher,
    *,
    get_url: Callable[[], str],
    save_url: Callable[[str], None],
    stats: StatsStore,
    bot: Bot,
) -> None:
    """Реєструє всі обробники. Викликається з ``bot.py``."""

    @dp.message(CommandStart())
    async def cmd_start(message: Message, state: FSMContext) -> None:
        url = get_url()
        await state.clear()
        stats.touch(message.from_user, "start")

        # Реплай-клавіатуру показуємо окремим повідомленням — вона з'явиться завжди.
        await message.answer(
            "Користуйтеся кнопками нижче 👇",
            reply_markup=build_reply_kb(url),
        )

        if not url:
            await message.answer(
                "HTTPS-посилання для Mini App поки не знайдено.\n"
                "Запустіть <code>python tunnel.py</code> або надішліть:\n"
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
                await message.answer(START_CAPTION, reply_markup=build_inline_kb(url))
        except Exception as exc:  # noqa: BLE001 — кнопки мають залишитися без фото
            print(f"[start] Не вдалося надіслати фото: {exc}", flush=True)
            await message.answer(
                START_CAPTION + "\n\n(фото тимчасово недоступне)",
                reply_markup=build_inline_kb(url),
            )

    @dp.message(Command("info"))
    @dp.message(F.text == "ℹ️ Інформація")
    async def cmd_info(message: Message) -> None:
        stats.touch(message.from_user, "info")
        await message.answer(INFO_TEXT, reply_markup=build_reply_kb(get_url()))

    @dp.message(Command("app"))
    @dp.message(F.text == "🚀 Відкрити")
    async def cmd_app(message: Message) -> None:
        url = get_url()
        if not url:
            await message.answer(
                "Посилання ще не задано. Використовуйте /seturl <https://...>",
                reply_markup=build_reply_kb(),
            )
            return
        stats.touch(message.from_user, "app")
        await message.answer("Запускайте Mini App:", reply_markup=build_inline_kb(url))

    @dp.message(Command("seturl"))
    async def cmd_seturl(message: Message) -> None:
        if stats.admin_ids and message.from_user.id not in stats.admin_ids:
            await message.answer("Ця команда доступна лише адміністраторам.")
            return
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip().startswith("https://"):
            await message.answer(
                "Вкажіть коректне HTTPS-посилання:\n"
                "<code>/seturl https://example.lhr.life</code>",
                parse_mode="HTML",
                reply_markup=build_reply_kb(get_url()),
            )
            return
        new_url = parts[1].strip().rstrip("/")
        save_url(new_url)
        await message.answer(
            f"Посилання оновлено: <code>{html.escape(new_url)}</code>",
            parse_mode="HTML",
            reply_markup=build_inline_kb(new_url),
        )

    @dp.message(Command("stats"))
    async def cmd_stats(message: Message) -> None:
        if not stats.admin_ids:
            await message.answer(
                "Адміністраторів не налаштовано. Додайте змінну "
                "<code>ADMIN_IDS</code> з вашим Telegram ID.",
                parse_mode="HTML",
            )
            return
        if message.from_user.id not in stats.admin_ids:
            await message.answer("Ця команда доступна лише адміністраторам.")
            return
        await message.answer(
            format_summary(stats.summary()),
            parse_mode="HTML",
            reply_markup=build_reply_kb(get_url()),
        )

    @dp.message(Command("feedbacks"))
    async def cmd_feedbacks(message: Message) -> None:
        if not stats.admin_ids or message.from_user.id not in stats.admin_ids:
            await message.answer("Ця команда доступна лише адміністраторам.")
            return
        items = stats.recent_feedbacks(5)
        if not items:
            await message.answer("Відгуків поки немає.")
            return
        lines = ["💬 <b>Останні відгуки</b>", ""]
        for item in items:
            label = FEEDBACK_CATEGORIES.get(item.get("category", ""), "✍️ Інше")
            who = html.escape(item.get("name") or "Невідомий")
            if item.get("username"):
                who += f" (@{html.escape(item['username'])})"
            text = html.escape(str(item.get("text", ""))[:300])
            lines.append(
                f"#{item.get('id')} [{label}] від {who}\n{text}\n"
                f"<i>{html.escape(str(item.get('created_at', ''))[:16])}</i>\n"
            )
        await message.answer("\n".join(lines), parse_mode="HTML")

    @dp.message(Command("feedback"))
    @dp.message(F.text == "💬 Відгук")
    async def feedback_start(message: Message, state: FSMContext) -> None:
        await state.set_state(Feedback.text)
        await message.answer(
            "💬 Напишіть ідею або повідомте про несправність.\n"
            "Просто надішліть текст — він потрапить адміністратору.",
            reply_markup=build_cancel_kb(),
        )

    @dp.callback_query(F.data == "feedback")
    async def feedback_callback(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        await state.set_state(Feedback.text)
        if callback.message:
            await callback.message.answer(
                "💬 Напишіть ідею або повідомте про несправність.",
                reply_markup=build_cancel_kb(),
            )

    @dp.message(Command("cancel"))
    @dp.message(F.text == "❌ Скасувати")
    async def cmd_cancel(message: Message, state: FSMContext) -> None:
        await state.clear()
        await message.answer("Скасовано.", reply_markup=build_reply_kb(get_url()))

    @dp.message(Feedback.text)
    async def process_feedback(message: Message, state: FSMContext) -> None:
        text = (message.text or "").strip()
        if len(text) < 3:
            await message.answer("Напишіть трохи більше тексту, будь ласка.")
            return
        text = text[:2000]
        await save_feedback(message.from_user, "other", text, stats, bot)
        await state.clear()
        await message.answer(
            "Дякуємо! Ваш відгук збережено і надіслано адміністратору 🙌",
            reply_markup=build_reply_kb(get_url()),
        )

    @dp.message(F.web_app_data)
    async def handle_web_app_data(message: Message) -> None:
        raw = message.web_app_data.data
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}

        kind = payload.get("type")
        if kind == "open":
            stats.touch(message.from_user, "webapp")
            return
        if kind != "feedback":
            return

        text = str(payload.get("text") or "").strip()[:2000]
        if len(text) < 3:
            await message.answer("Відгук занадто короткий — спробуйте ще раз.")
            return
        category = str(payload.get("category") or "other")
        if category not in FEEDBACK_CATEGORIES:
            category = "other"
        # sendData доступний лише коли застосунок відкрито з бота —
        # тому це також підтвердження відкриття Mini App.
        stats.touch(message.from_user, "webapp")
        await save_feedback(message.from_user, category, text, stats, bot)
        await message.answer(
            "Дякуємо за відгук! Ми його обов'язково розглянемо 🙌",
            reply_markup=build_reply_kb(get_url()),
        )

    @dp.message(F.text)
    async def fallback_text(message: Message, state: FSMContext) -> None:
        # Коли користувач пише відгук, звичайна відповідь не потрібна.
        if await state.get_state() == Feedback.text.state:
            return
        stats.touch(message.from_user, "message")
        url = get_url()
        if url:
            await message.answer(
                "Натисніть «Відкрити», щоб запустити Mini App:",
                reply_markup=build_inline_kb(url),
            )
        else:
            await message.answer(
                "Введіть /start або задайте посилання через /seturl.",
                reply_markup=build_reply_kb(),
            )


async def save_feedback(
    user: User,
    category: str,
    text: str,
    stats: StatsStore,
    bot: Bot,
) -> None:
    """Зберегти відгук і сповістити адміністраторів."""
    entry_id = stats.add_feedback(user, category, text)
    label = FEEDBACK_CATEGORIES.get(category, "✍️ Інше")
    notification = (
        f"💬 <b>Новий відгук #{entry_id}</b> [{label}]\n"
        f"Від: {html.escape(user_label(user))}\n\n"
        f"{html.escape(text)}"
    )
    for admin_id in stats.admin_ids:
        try:
            await bot.send_message(admin_id, notification, parse_mode="HTML")
        except Exception as exc:  # noqa: BLE001 — один адмін не блокує інших
            print(f"[feedback] Не вдалося повідомити {admin_id}: {exc}", flush=True)
