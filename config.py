"""
Конфигурация бота. Все секреты берутся из переменных окружения (.env).
Ничего не хардкодим — ключи в коде = угнанный аккаунт.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # python-dotenv не обязателен, если переменные заданы в системе
    pass

BASE_DIR = Path(__file__).parent


def _env(name: str, default: str | None = None, required: bool = False) -> str:
    value = os.getenv(name, default)
    if required and not value:
        raise RuntimeError(
            f"Не задана переменная окружения {name}. "
            f"Скопируй .env.example в .env и заполни его."
        )
    return value or ""


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        raise RuntimeError(f"{name} должен быть числом, а не {raw!r}")


def _env_list(name: str, default: list[str]) -> list[str]:
    raw = os.getenv(name)
    if not raw:
        return default
    return [item.strip() for item in raw.split(",") if item.strip()]


def _env_hours(name: str, default: list[str]) -> list[int]:
    """Часы публикации. Ошибку ловим здесь, а не через сутки в момент постинга."""
    hours = []
    for item in _env_list(name, default):
        try:
            hour = int(item)
        except ValueError:
            raise RuntimeError(
                f"{name} должен быть списком часов через запятую (например 10,15,20), "
                f"а не {item!r}"
            )
        if not 0 <= hour <= 23:
            raise RuntimeError(f"{name}: час {hour} вне диапазона 0..23")
        hours.append(hour)
    return sorted(set(hours))


@dataclass
class Config:
    # --- Способ авторизации: "oauth1" или "oauth2" ---
    # oauth1 — токен не протухает, ничего обновлять не надо. Предпочтительно.
    # oauth2 — токен живёт ~2 часа, бот сам обновляет его по refresh token.
    auth_mode: str = field(default_factory=lambda: _env("AUTH_MODE", "oauth1").lower())

    # --- OAuth 1.0a User Context (нужно при AUTH_MODE=oauth1) ---
    x_api_key: str = field(default_factory=lambda: _env("X_API_KEY"))
    x_api_secret: str = field(default_factory=lambda: _env("X_API_SECRET"))
    x_access_token: str = field(default_factory=lambda: _env("X_ACCESS_TOKEN"))
    x_access_secret: str = field(default_factory=lambda: _env("X_ACCESS_TOKEN_SECRET"))

    # --- OAuth 2.0 User Context (нужно при AUTH_MODE=oauth2) ---
    x_client_id: str = field(default_factory=lambda: _env("X_CLIENT_ID"))
    x_client_secret: str = field(default_factory=lambda: _env("X_CLIENT_SECRET"))
    # Стартовый refresh token. Дальше бот хранит актуальный в token.json:
    # X меняет refresh token при каждом обновлении, значение из .env устаревает
    # после первого же запуска и нужно только для первичной загрузки.
    x_refresh_token: str = field(default_factory=lambda: _env("X_REFRESH_TOKEN"))

    # Ожидаемый аккаунт. На то, куда уйдёт пост, НЕ влияет — аккаунт целиком
    # определяется Access Token. Нужен, чтобы `check` поймал чужие токены
    # до того, как пост уйдёт не туда.
    x_username: str = field(default_factory=lambda: _env("X_USERNAME").lstrip("@"))

    # --- Источник контента: "file" (список готовых постов) или "ai" (генерация) ---
    content_mode: str = field(default_factory=lambda: _env("CONTENT_MODE", "file"))

    # --- Генерация через Claude API (нужно только при CONTENT_MODE=ai) ---
    anthropic_api_key: str = field(default_factory=lambda: _env("ANTHROPIC_API_KEY"))
    model: str = field(default_factory=lambda: _env("MODEL", "claude-sonnet-5"))
    account_topic: str = field(
        default_factory=lambda: _env("ACCOUNT_TOPIC", "технологии, ИИ и разработка")
    )
    account_tone: str = field(
        default_factory=lambda: _env("ACCOUNT_TONE", "живой, без канцелярита, без хэштегов и эмодзи-спама")
    )
    post_language: str = field(default_factory=lambda: _env("POST_LANGUAGE", "русский"))

    # --- Расписание ---
    posts_per_day: int = field(default_factory=lambda: _env_int("POSTS_PER_DAY", 3))
    # Часы, в которые бот постит (локальное время сервера)
    active_hours: list[int] = field(
        default_factory=lambda: _env_hours("ACTIVE_HOURS", ["10", "15", "20"])
    )
    # Случайный сдвиг в минутах, чтобы посты не выходили секунда-в-секунду
    jitter_minutes: int = field(default_factory=lambda: _env_int("JITTER_MINUTES", 25))

    # --- Ограничения / безопасность ---
    max_posts_per_day: int = field(default_factory=lambda: _env_int("MAX_POSTS_PER_DAY", 5))
    dry_run: bool = field(default_factory=lambda: _env("DRY_RUN", "false").lower() == "true")

    # --- Файлы ---
    posts_file: Path = BASE_DIR / "posts.txt"
    history_file: Path = BASE_DIR / "history.json"
    token_file: Path = BASE_DIR / "token.json"
    log_file: Path = BASE_DIR / "bot.log"

    def validate(self) -> None:
        if self.auth_mode not in ("oauth1", "oauth2"):
            raise RuntimeError("AUTH_MODE должен быть 'oauth1' или 'oauth2'")

        if self.auth_mode == "oauth1":
            missing = [
                name for name, value in (
                    ("X_API_KEY", self.x_api_key),
                    ("X_API_SECRET", self.x_api_secret),
                    ("X_ACCESS_TOKEN", self.x_access_token),
                    ("X_ACCESS_TOKEN_SECRET", self.x_access_secret),
                ) if not value
            ]
            if missing:
                raise RuntimeError(
                    f"AUTH_MODE=oauth1, но не заданы: {', '.join(missing)}. "
                    f"Возьми их в developer.x.com → Keys and tokens."
                )
        else:
            if not self.x_client_id:
                raise RuntimeError(
                    "AUTH_MODE=oauth2, но не задан X_CLIENT_ID. "
                    "Он в developer.x.com → Keys and tokens → OAuth 2.0 Client ID and Client Secret."
                )
            if not self.x_refresh_token and not self.token_file.exists():
                raise RuntimeError(
                    "AUTH_MODE=oauth2, но нет ни X_REFRESH_TOKEN в .env, ни token.json. "
                    "Пройди авторизацию и положи refresh token в X_REFRESH_TOKEN."
                )

        if self.content_mode not in ("file", "ai"):
            raise RuntimeError("CONTENT_MODE должен быть 'file' или 'ai'")
        if self.content_mode == "ai" and not self.anthropic_api_key:
            raise RuntimeError("CONTENT_MODE=ai, но не задан ANTHROPIC_API_KEY")
        if not self.active_hours:
            raise RuntimeError("ACTIVE_HOURS пуст — укажи хотя бы один час, например 10,15,20")
        if self.posts_per_day < 1:
            raise RuntimeError("POSTS_PER_DAY должен быть не меньше 1")
        if self.jitter_minutes < 0:
            raise RuntimeError("JITTER_MINUTES не может быть отрицательным")
        if self.posts_per_day > len(self.active_hours):
            raise RuntimeError(
                f"POSTS_PER_DAY={self.posts_per_day}, но в ACTIVE_HOURS всего "
                f"{len(self.active_hours)} слотов. Добавь часов или уменьши количество постов."
            )
        if self.posts_per_day > self.max_posts_per_day:
            raise RuntimeError("POSTS_PER_DAY больше, чем MAX_POSTS_PER_DAY")


def load_config() -> Config:
    cfg = Config()
    cfg.validate()
    return cfg
