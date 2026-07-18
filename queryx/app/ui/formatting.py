from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any


_STATUS_CLASSES = {
    "ready": "success",
    "completed": "success",
    "online": "success",
    "ok": "success",
    "created": "neutral",
    "queued": "neutral",
    "acquiring": "progress",
    "inspecting": "progress",
    "normalizing": "progress",
    "registering": "progress",
    "validating": "progress",
    "leased": "progress",
    "retry_wait": "warning",
    "partial": "warning",
    "stale": "warning",
    "degraded": "warning",
    "failed": "danger",
    "cancelled": "danger",
    "offline": "danger",
}


def format_timestamp(value: datetime | None) -> str:
    if value is None:
        return "—"
    return value.astimezone().strftime("%d/%m/%Y %H:%M:%S")


def format_bytes(value: int | None) -> str:
    if value is None:
        return "—"
    size = float(value)
    units = ("B", "KiB", "MiB", "GiB")
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{value} B"


def abbreviate(value: str | None, length: int = 12) -> str:
    if not value:
        return "—"
    return value if len(value) <= length else f"{value[:length]}…"


def status_class(value: str) -> str:
    return _STATUS_CLASSES.get(value.lower(), "neutral")


def format_value(value: Any, max_length: int = 160) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (datetime, date)):
        rendered = value.isoformat()
    elif isinstance(value, (dict, list, tuple)):
        rendered = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    else:
        rendered = str(value)
    if len(rendered) > max_length:
        return f"{rendered[:max_length]}…"
    return rendered


def structured_message(value: dict[str, Any] | None) -> str | None:
    if not value:
        return None
    code = value.get("code")
    message = value.get("message") or "Errore non specificato"
    return f"{code}: {message}" if code else str(message)

