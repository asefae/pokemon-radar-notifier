"""Small, testable Telegram Bot API client."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None  # type: ignore[assignment]


@dataclass(frozen=True)
class TelegramResult:
    success: bool
    status_code: int | None
    ok: bool
    description: str


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str, timeout: float = 10.0) -> None:
        self.token = token.strip()
        self.chat_id = str(chat_id).strip()
        self.timeout = timeout

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    @property
    def token_length(self) -> int:
        return len(self.token)

    def _post(self, method: str, payload: dict[str, Any]) -> TelegramResult:
        if not self.token:
            return TelegramResult(False, None, False, "Bot Token не заполнен")
        if requests is None:
            return TelegramResult(False, None, False, "Пакет requests не установлен")
        url = f"https://api.telegram.org/bot{self.token}/{method}"
        try:
            response = requests.post(url, json=payload, timeout=self.timeout)
        except requests.RequestException as exc:
            return TelegramResult(False, None, False, f"Сетевая ошибка: {exc}")
        try:
            data: Any = response.json()
        except ValueError:
            return TelegramResult(False, response.status_code, False, "Telegram вернул не JSON")
        ok = bool(data.get("ok"))
        description = str(data.get("description", ""))
        return TelegramResult(
            ok and response.ok,
            response.status_code,
            ok,
            description or ("OK" if ok else "Telegram сообщил об ошибке"),
        )

    def send_message(self, text: str) -> TelegramResult:
        if not self.chat_id:
            return TelegramResult(False, None, False, "Chat ID не заполнен")
        return self._post("sendMessage", {"chat_id": str(self.chat_id), "text": text})

    def get_me(self) -> TelegramResult:
        return self._post("getMe", {})

    def diagnose(self) -> tuple[TelegramResult, TelegramResult | None]:
        auth = self.get_me()
        if not auth.success:
            return auth, None
        return auth, self.send_message("Тестовое уведомление Pokemon Radar работает ✅")

    # Backward-compatible API.
    def send(self, text: str) -> bool:
        return self.send_message(text).success
