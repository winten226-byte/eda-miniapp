# -*- coding: utf-8 -*-
"""Облік користувачів бота та збереження відгуків.

Дані зберігаються у JSON-файлі (за замовчуванням ``stats.json`` поруч із кодом).
Файл не комітиться в git: див. ``.gitignore``.
"""
from __future__ import annotations

import json
import os
import threading
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent

# Адміністратор за замовчуванням (додається до ADMIN_IDS з оточення).
DEFAULT_ADMIN_IDS = {1591310608}

# Подія -> лічильник у профілі користувача.
EVENT_COUNTERS = {
    "start": "starts",
    "app": "opens",
    "webapp": "opens",
    "info": "infos",
    "feedback": "feedbacks",
}


def parse_admin_ids(raw: str | None = None) -> set[int]:
    """Розбирає ADMIN_IDS: ``123, 456`` або ``123;456``."""
    if raw is None:
        raw = os.getenv("ADMIN_IDS", "")
    ids: set[int] = set(DEFAULT_ADMIN_IDS)
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if part.lstrip("-").isdigit():
            ids.add(int(part))
    return ids


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utcnow().isoformat(timespec="seconds")


def parse_iso(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


class StatsStore:
    """Простий локальний лічильник із потокобезпечним записом."""

    def __init__(self, path: Path | None = None, admin_ids: set[int] | None = None):
        if path is None:
            raw_path = (os.getenv("STATS_FILE") or "").strip()
            path = Path(raw_path) if raw_path else HERE / "stats.json"
        self.path = path
        self.admin_ids = parse_admin_ids() if admin_ids is None else admin_ids
        self._lock = threading.Lock()
        self.data: dict = self._load()

    def _load(self) -> dict:
        if not self.path.exists():
            return {"users": {}, "feedbacks": []}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8-sig"))
            if not isinstance(raw, dict):
                raise ValueError("некоректна структура файлу")
            if not isinstance(raw.get("users"), dict):
                raw["users"] = {}
            if not isinstance(raw.get("feedbacks"), list):
                raw["feedbacks"] = []
            return raw
        except (OSError, ValueError, json.JSONDecodeError):
            # Пошкоджений файл не повинен валити бота.
            return {"users": {}, "feedbacks": []}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(self.path)

    @staticmethod
    def _user_fields(user: SimpleNamespace | None) -> dict:
        if user is None:
            return {"username": "", "name": "Невідомий"}
        return {
            "username": getattr(user, "username", None) or "",
            "name": getattr(user, "full_name", None)
            or getattr(user, "first_name", None)
            or "Невідомий",
        }

    def _touch(self, user: SimpleNamespace | None, event: str) -> None:
        now = iso_now()
        uid = str(getattr(user, "id", "unknown"))
        users = self.data["users"]
        record = users.get(uid)
        if record is None:
            record = {
                **self._user_fields(user),
                "first_seen": now,
                "last_seen": now,
                "events": 0,
                "starts": 0,
                "opens": 0,
                "infos": 0,
                "feedbacks": 0,
                "lat": None,
                "lon": None,
                "address": "",
            }
            users[uid] = record
        else:
            record.update(self._user_fields(user))
            record["last_seen"] = now
        record["events"] = record.get("events", 0) + 1
        counter = EVENT_COUNTERS.get(event)
        if counter:
            record[counter] = record.get(counter, 0) + 1

    def touch(self, user: SimpleNamespace | None, event: str = "message") -> None:
        """Зарахувати активність користувача (наприклад, ``start`` чи ``app``)."""
        with self._lock:
            self._touch(user, event)
            self._save()

    def set_location(
        self,
        user: SimpleNamespace | None,
        lat: float,
        lon: float,
        address: str = "",
    ) -> None:
        """Зберегти геолокацію/адресу користувача."""
        with self._lock:
            uid = str(getattr(user, "id", "unknown"))
            self._touch(user, "location")
            record = self.data["users"][uid]
            record["lat"] = float(lat)
            record["lon"] = float(lon)
            record["address"] = str(address or "").strip()[:200]
            self._save()

    def add_feedback(
        self,
        user: SimpleNamespace | None,
        category: str,
        text: str,
    ) -> int:
        """Зберегти відгук і повернути його номер."""
        with self._lock:
            self._touch(user, "feedback")
            feedbacks = self.data["feedbacks"]
            entry = {
                "id": len(feedbacks) + 1,
                "user_id": getattr(user, "id", None),
                "username": getattr(user, "username", None) or "",
                "name": self._user_fields(user)["name"],
                "category": category,
                "text": text,
                "created_at": iso_now(),
            }
            feedbacks.append(entry)
            self._save()
            return entry["id"]

    def summary(self) -> dict:
        """Повернути зріз статистики для адмінської команди."""
        with self._lock:
            data = deepcopy(self.data)
        users = data["users"]
        now = utcnow()
        day_ago = now - timedelta(days=1)
        week_ago = now - timedelta(days=7)

        def is_recent(value: str, since: datetime) -> bool:
            parsed = parse_iso(value)
            return parsed is not None and parsed >= since

        recent_users = sorted(
            users.items(),
            key=lambda item: parse_iso(item[1].get("last_seen", "")) or now,
            reverse=True,
        )
        return {
            "total_users": len(users),
            "new_24h": sum(
                is_recent(record.get("first_seen", ""), day_ago)
                for record in users.values()
            ),
            "new_7d": sum(
                is_recent(record.get("first_seen", ""), week_ago)
                for record in users.values()
            ),
            "active_24h": sum(
                is_recent(record.get("last_seen", ""), day_ago)
                for record in users.values()
            ),
            "starts": sum(record.get("starts", 0) for record in users.values()),
            "opens": sum(record.get("opens", 0) for record in users.values()),
            "feedbacks": sum(record.get("feedbacks", 0) for record in users.values()),
            "saved_feedbacks": len(data["feedbacks"]),
            "recent_users": recent_users[:5],
        }

    def feedback_total(self) -> int:
        with self._lock:
            return len(self.data["feedbacks"])

    def recent_feedbacks(self, limit: int = 10, page: int = 1) -> list[dict]:
        """Повертає сторінку відгуків: page=1 — найновіші."""
        page = max(1, page)
        with self._lock:
            feedbacks = self.data["feedbacks"]
            end = max(0, len(feedbacks) - (page - 1) * limit)
            start = max(0, end - limit)
            return deepcopy(feedbacks[start:end][::-1])
