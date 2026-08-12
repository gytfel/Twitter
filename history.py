"""
История публикаций: защита от дублей (X отклоняет одинаковые твиты)
и жёсткий лимит постов в сутки.
"""

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)


def normalize(text: str) -> str:
    """Приводим текст к каноническому виду, чтобы ловить почти-дубли."""
    text = text.lower().strip()
    text = re.sub(r"https?://\S+", "", text)   # ссылки не считаем
    text = re.sub(r"[^\w\s]", "", text, flags=re.UNICODE)  # пунктуация
    text = re.sub(r"\s+", " ", text)
    return text


def text_hash(text: str) -> str:
    return hashlib.sha256(normalize(text).encode("utf-8")).hexdigest()[:16]


class History:
    def __init__(self, path: Path):
        self.path = path
        self.entries: list[dict] = []
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            self.entries = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            log.warning("Не смог прочитать %s (%s). Начинаю историю заново.", self.path, e)
            self.entries = []

    def _save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(self.entries, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        tmp.replace(self.path)  # атомарная запись, чтобы не потерять файл при сбое

    def add(self, text: str, tweet_id: str | None) -> None:
        self.entries.append(
            {
                "hash": text_hash(text),
                "text": text,
                "tweet_id": tweet_id,
                "posted_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        self._save()

    def is_duplicate(self, text: str, lookback: int = 300) -> bool:
        h = text_hash(text)
        return any(e.get("hash") == h for e in self.entries[-lookback:])

    def posted_today(self) -> int:
        today = datetime.now(timezone.utc).date()
        count = 0
        for e in self.entries:
            try:
                dt = datetime.fromisoformat(e["posted_at"])
            except (KeyError, ValueError):
                continue
            if dt.date() == today:
                count += 1
        return count

    def recent_texts(self, n: int = 15) -> list[str]:
        """Последние посты — отдаём в генератор, чтобы он не повторялся по смыслу."""
        return [e["text"] for e in self.entries[-n:] if e.get("text")]
