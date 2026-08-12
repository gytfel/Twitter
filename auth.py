"""
OAuth 2.0 User Context: получение и обновление access token.

X выдаёт короткоживущий access token (~2 часа) и refresh token к нему.
Ключевая особенность: при каждом обновлении X возвращает НОВЫЙ refresh token
и гасит старый. Потерять новый — значит потерять доступ насовсем, до ручной
переавторизации. Поэтому сохраняем его на диск сразу же, до всякой публикации.

Для OAuth 1.0a этот модуль не нужен: там токен не протухает.
"""

import json
import logging
import time
from pathlib import Path

import requests

log = logging.getLogger(__name__)

TOKEN_URL = "https://api.x.com/2/oauth2/token"

# Обновляем заранее, за 5 минут до истечения: пост не должен упасть
# из-за токена, протухшего между проверкой и отправкой.
EXPIRY_MARGIN_SEC = 300


class AuthError(Exception):
    """Токен получить не удалось."""


class TokenStore:
    """Токены между запусками. В GitHub Actions файл коммитится в репозиторий —
    иначе следующий запуск не сможет войти, потому что старый refresh token уже погашен."""

    def __init__(self, path: Path):
        self.path = path
        self.data: dict = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            log.warning("Не смог прочитать %s (%s). Возьму refresh token из .env.", self.path, e)
            self.data = {}

    def save(self, access_token: str, refresh_token: str, expires_in: int) -> None:
        self.data = {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_at": int(time.time()) + int(expires_in),
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        tmp.replace(self.path)  # атомарно: половина токена хуже, чем его отсутствие

    @property
    def access_token(self) -> str:
        return self.data.get("access_token", "")

    @property
    def refresh_token(self) -> str:
        return self.data.get("refresh_token", "")

    def access_token_valid(self) -> bool:
        if not self.access_token:
            return False
        return time.time() < self.data.get("expires_at", 0) - EXPIRY_MARGIN_SEC


def _refresh(client_id: str, client_secret: str, refresh_token: str) -> dict:
    """Меняет refresh token на свежую пару токенов."""
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
    }
    # Confidential client (Web App / Automated App) подтверждает себя Basic-авторизацией.
    # Public client секрета не имеет — тогда хватает client_id в теле запроса.
    auth = (client_id, client_secret) if client_secret else None

    try:
        response = requests.post(TOKEN_URL, data=data, auth=auth, timeout=30)
    except requests.exceptions.RequestException as e:
        raise AuthError(f"Нет связи с {TOKEN_URL}: {e}") from e

    if response.status_code == 200:
        payload = response.json()
        if not payload.get("access_token"):
            raise AuthError(f"X вернул ответ без access_token: {payload}")
        return payload

    detail = response.text[:300]
    if response.status_code in (400, 401):
        raise AuthError(
            f"X отклонил refresh token ({response.status_code}): {detail}\n"
            f"  Обычно это значит, что токен уже использован, отозван или протух.\n"
            f"  Пройди авторизацию заново и положи новый refresh token в X_REFRESH_TOKEN."
        )
    raise AuthError(f"Обновление токена не удалось ({response.status_code}): {detail}")


def get_access_token(cfg) -> str:
    """Действующий access token: из кэша, а если протух — обновляет и сохраняет."""
    store = TokenStore(cfg.token_file)

    if store.access_token_valid():
        return store.access_token

    refresh_token = store.refresh_token or cfg.x_refresh_token
    if not refresh_token:
        raise AuthError(
            "Нет refresh token: заполни X_REFRESH_TOKEN в .env "
            "или положи рядом token.json с прошлого запуска."
        )

    log.info("Access token протух или отсутствует, обновляю.")
    payload = _refresh(cfg.x_client_id, cfg.x_client_secret, refresh_token)

    # Сохраняем ДО публикации. Старый refresh token X уже погасил, и если мы
    # уроним новый (упавший пост, сбой сети, kill -9) — войти будет нечем.
    store.save(
        payload["access_token"],
        payload.get("refresh_token", refresh_token),
        payload.get("expires_in", 7200),
    )
    log.info("Токен обновлён, действует %s сек.", payload.get("expires_in", 7200))
    return payload["access_token"]
