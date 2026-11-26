from __future__ import annotations

import base64
import binascii
import json
from datetime import datetime, timezone
from typing import Any, NoReturn
from uuid import UUID

from app.core.errors import AppError
from app.core.limits import MAX_CURSOR_LENGTH
from app.services.catalog import CatalogService


_CURSOR_VERSION = 1


def bad_user_input(message: str) -> NoReturn:
    raise AppError(status_code=422, code="VALIDATION_ERROR", message=message)


def validate_limit(limit: int, *, maximum: int) -> int:
    if not 1 <= limit <= maximum:
        bad_user_input(f"limit harus antara 1 dan {maximum}.")
    return limit


def normalize_search(search: str | None) -> str | None:
    try:
        return CatalogService.normalize_search(search)
    except ValueError as exc:
        bad_user_input(str(exc))


def encode_menu_cursor(last_id: str, search: str | None) -> str:
    return _encode(
        {
            "v": _CURSOR_VERSION,
            "kind": "menu",
            "last_id": last_id,
            "search": normalize_search(search),
        }
    )


def decode_menu_cursor(cursor: str | None, search: str | None) -> str | None:
    if cursor is None:
        return None
    payload = _decode(cursor)
    if set(payload) != {"v", "kind", "last_id", "search"}:
        bad_user_input("Cursor menu tidak valid.")
    if payload.get("v") != _CURSOR_VERSION or payload.get("kind") != "menu":
        bad_user_input("Cursor menu tidak valid.")
    last_id = payload.get("last_id")
    stored_search = payload.get("search")
    if (
        not isinstance(last_id, str)
        or not 1 <= len(last_id) <= 64
        or not all(
            character.isascii()
            and (character.isalnum() or character in "_-")
            for character in last_id
        )
    ):
        bad_user_input("Cursor menu tidak valid.")
    if stored_search is not None and not isinstance(stored_search, str):
        bad_user_input("Cursor menu tidak valid.")
    if stored_search != normalize_search(search):
        bad_user_input("Cursor tidak sesuai dengan konteks pencarian.")
    return last_id


def encode_history_cursor(created_at: datetime, recommendation_id: UUID, user_id: UUID) -> str:
    if created_at.tzinfo is None or created_at.utcoffset() is None:
        raise ValueError("created_at must be timezone-aware")
    timestamp = created_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return _encode(
        {
            "v": _CURSOR_VERSION,
            "kind": "history",
            "created_at": timestamp,
            "id": str(recommendation_id),
            "user_id": str(user_id),
        }
    )


def decode_history_cursor(
    cursor: str | None,
    user_id: UUID,
) -> tuple[datetime, UUID] | None:
    if cursor is None:
        return None
    payload = _decode(cursor)
    if set(payload) != {"v", "kind", "created_at", "id", "user_id"}:
        bad_user_input("Cursor riwayat tidak valid.")
    if payload.get("v") != _CURSOR_VERSION or payload.get("kind") != "history":
        bad_user_input("Cursor riwayat tidak valid.")
    if payload.get("user_id") != str(user_id):
        bad_user_input("Cursor tidak sesuai dengan pengguna aktif.")
    created_at_value = payload.get("created_at")
    recommendation_id_value = payload.get("id")
    if not isinstance(created_at_value, str) or not isinstance(recommendation_id_value, str):
        bad_user_input("Cursor riwayat tidak valid.")
    try:
        created_at = datetime.fromisoformat(created_at_value.replace("Z", "+00:00"))
        recommendation_id = UUID(recommendation_id_value)
    except (ValueError, TypeError):
        bad_user_input("Cursor riwayat tidak valid.")
    if created_at.tzinfo is None or created_at.utcoffset() is None:
        bad_user_input("Cursor riwayat tidak valid.")
    return created_at.astimezone(timezone.utc), recommendation_id


def _encode(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _decode(cursor: str) -> dict[str, Any]:
    if not isinstance(cursor, str) or not cursor or len(cursor) > MAX_CURSOR_LENGTH:
        bad_user_input("Cursor tidak valid.")
    if not all(
        character.isascii()
        and (character.isalnum() or character in "_-")
        for character in cursor
    ):
        bad_user_input("Cursor tidak valid.")
    try:
        padding = "=" * (-len(cursor) % 4)
        raw = base64.b64decode(cursor + padding, altchars=b"-_", validate=True)
        if len(raw) > MAX_CURSOR_LENGTH:
            raise ValueError("cursor payload too large")
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
        )
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        bad_user_input("Cursor tidak valid.")
    if not isinstance(payload, dict):
        bad_user_input("Cursor tidak valid.")
    return payload
