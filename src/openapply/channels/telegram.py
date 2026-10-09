"""Private-chat Telegram adapter using the official Bot API long-polling flow."""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import httpx

from openapply.conversations.service import ConversationService
from openapply.storage.database import Database, utc_now

MAX_TELEGRAM_TEXT = 4096


class TelegramError(Exception):
    pass


def _code_hash(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


class TelegramChannel:
    def __init__(
        self,
        token: str,
        database: Database | None = None,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not token.strip():
            raise TelegramError("Telegram bot token is empty")
        self.database = database or Database()
        self.conversations = ConversationService(self.database)
        self._base_url = f"https://api.telegram.org/bot{token.strip()}"
        self._client = client

    def create_pairing_code(self, *, valid_minutes: int = 15) -> str:
        code = f"{secrets.randbelow(100_000_000):08d}"
        now = datetime.now(UTC)
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                "DELETE FROM channel_pairing_codes WHERE channel = 'telegram' "
                "AND (consumed_at IS NOT NULL OR expires_at < ?)",
                (now.isoformat(),),
            )
            connection.execute(
                "INSERT INTO channel_pairing_codes "
                "(code_hash, channel, expires_at, created_at) VALUES (?, 'telegram', ?, ?)",
                (
                    _code_hash(code),
                    (now + timedelta(minutes=valid_minutes)).isoformat(),
                    now.isoformat(),
                ),
            )
        return code

    def disconnect(self) -> int:
        with self.database.transaction(immediate=True) as connection:
            result = connection.execute(
                "UPDATE channel_bindings SET revoked_at = ? "
                "WHERE channel = 'telegram' AND revoked_at IS NULL",
                (utc_now(),),
            )
        return result.rowcount

    def handle_update(
        self, update: dict[str, Any], *, timezone: str = "UTC"
    ) -> tuple[str, str] | None:
        update_id = update.get("update_id")
        message = update.get("message")
        if not isinstance(update_id, int) or not isinstance(message, dict):
            return None
        chat = message.get("chat")
        sender = message.get("from")
        text = message.get("text")
        if not isinstance(chat, dict) or chat.get("type") != "private":
            return None
        if not isinstance(sender, dict) or not isinstance(text, str):
            return None
        chat_id = str(chat.get("id", ""))
        user_id = str(sender.get("id", ""))
        if not chat_id or not user_id:
            return None
        if text.startswith("/start"):
            parts = text.split(maxsplit=1)
            if len(parts) != 2 or not self._consume_pairing_code(parts[1], user_id, chat_id):
                return chat_id, "Pairing failed or expired. Create a new code in OpenApply."
            return (
                chat_id,
                "OpenApply is paired. Ask for today's report, application details, or start "
                "your interview. Application approval stays in your private OpenApply app.",
            )
        if not self._is_bound(user_id, chat_id):
            return chat_id, "This chat is not paired with OpenApply."
        reply = self.conversations.chat(
            text,
            channel="telegram",
            external_id=str(update_id),
            timezone=timezone,
        )
        return chat_id, reply.assistant_message.text

    def _consume_pairing_code(self, code: str, user_id: str, chat_id: str) -> bool:
        now = utc_now()
        with self.database.transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT * FROM channel_pairing_codes WHERE code_hash = ? AND channel = 'telegram'",
                (_code_hash(code.strip()),),
            ).fetchone()
            if row is None or row["consumed_at"] is not None or str(row["expires_at"]) < now:
                return False
            connection.execute(
                "UPDATE channel_pairing_codes SET consumed_at = ? WHERE code_hash = ?",
                (now, str(row["code_hash"])),
            )
            connection.execute(
                "INSERT INTO channel_bindings "
                "(id, channel, external_user_id, external_chat_id, capabilities_json, created_at) "
                "VALUES (?, 'telegram', ?, ?, ?, ?) "
                "ON CONFLICT(channel, external_user_id, external_chat_id) DO UPDATE SET "
                "revoked_at = NULL, capabilities_json = excluded.capabilities_json",
                (
                    str(uuid4()),
                    user_id,
                    chat_id,
                    json.dumps(["chat", "reports", "interview", "pause"]),
                    now,
                ),
            )
        return True

    def _is_bound(self, user_id: str, chat_id: str) -> bool:
        with self.database.read() as connection:
            row = connection.execute(
                "SELECT 1 FROM channel_bindings WHERE channel = 'telegram' "
                "AND external_user_id = ? AND external_chat_id = ? AND revoked_at IS NULL",
                (user_id, chat_id),
            ).fetchone()
        return row is not None

    def _cursor(self) -> int | None:
        with self.database.read() as connection:
            row = connection.execute(
                "SELECT cursor FROM channel_state WHERE channel = 'telegram'"
            ).fetchone()
        if row is None or row["cursor"] is None:
            return None
        return int(row["cursor"])

    def _save_cursor(self, value: int) -> None:
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                "INSERT INTO channel_state(channel, cursor, updated_at) VALUES ('telegram', ?, ?) "
                "ON CONFLICT(channel) DO UPDATE SET cursor = excluded.cursor, "
                "updated_at = excluded.updated_at",
                (str(value), utc_now()),
            )

    async def run_forever(self, *, timezone: str = "UTC") -> None:
        owned_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=httpx.Timeout(40.0))
        try:
            while True:
                payload: dict[str, object] = {
                    "timeout": 30,
                    "allowed_updates": ["message"],
                }
                cursor = self._cursor()
                if cursor is not None:
                    payload["offset"] = cursor
                try:
                    response = await client.post(f"{self._base_url}/getUpdates", json=payload)
                    response.raise_for_status()
                    body = response.json()
                    if not body.get("ok") or not isinstance(body.get("result"), list):
                        raise TelegramError("Telegram returned an invalid getUpdates response")
                    for raw_update in body["result"]:
                        if not isinstance(raw_update, dict):
                            continue
                        handled = self.handle_update(raw_update, timezone=timezone)
                        if handled is not None:
                            chat_id, reply = handled
                            await self._send(client, chat_id, reply)
                        update_id = raw_update.get("update_id")
                        if isinstance(update_id, int):
                            self._save_cursor(update_id + 1)
                except (httpx.HTTPError, TelegramError):
                    await asyncio.sleep(5)
        finally:
            if owned_client:
                await client.aclose()

    async def _send(self, client: httpx.AsyncClient, chat_id: str, text: str) -> None:
        chunks = [text[i : i + MAX_TELEGRAM_TEXT] for i in range(0, len(text), MAX_TELEGRAM_TEXT)]
        for chunk in chunks or [""]:
            response = await client.post(
                f"{self._base_url}/sendMessage",
                json={"chat_id": chat_id, "text": chunk, "protect_content": True},
            )
            response.raise_for_status()
            body = response.json()
            if not body.get("ok"):
                raise TelegramError("Telegram rejected sendMessage")
