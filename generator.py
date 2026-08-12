"""
Генерация текста поста.

Два режима:
  file — берём готовые посты из posts.txt (надёжно, бесплатно, полный контроль)
  ai   — генерируем через Claude API (бесконечный контент, но нужен ключ и деньги)
"""

import logging
import random
from pathlib import Path

from history import History

log = logging.getLogger(__name__)

MAX_LEN = 280


class GeneratorError(Exception):
    pass


# ---------------------------------------------------------------- режим "file"

def load_pool(path: Path) -> list[str]:
    """Посты в posts.txt разделяются строкой '---' на отдельной строке."""
    if not path.exists():
        raise GeneratorError(f"Нет файла {path}")

    raw = path.read_text(encoding="utf-8")
    posts = []
    for chunk in raw.split("\n---\n"):
        # Комментарии вырезаем построчно: иначе шапка файла «съедает» первый пост
        text = "\n".join(
            line for line in chunk.splitlines() if not line.lstrip().startswith("#")
        ).strip()

        if not text:
            continue
        if len(text) > MAX_LEN:
            log.warning("Пост длиннее %s символов, пропускаю: %.60s...", MAX_LEN, text)
            continue
        posts.append(text)

    if not posts:
        raise GeneratorError(f"В {path} не нашлось ни одного валидного поста")
    return posts


def from_file(path: Path, history: History) -> str:
    pool = load_pool(path)
    unused = [p for p in pool if not history.is_duplicate(p)]

    if unused:
        return random.choice(unused)

    # Круг закончился — начинаем заново, но с самого давнего поста,
    # чтобы ни в коем случае не повторить вчерашний.
    log.warning("Все %s постов из файла уже опубликованы. Начинаю круг заново.", len(pool))
    recent = history.recent_texts(len(pool))

    def last_used(post: str) -> int:
        return recent.index(post) if post in recent else -1

    return min(pool, key=last_used)


# ------------------------------------------------------------------ режим "ai"

SYSTEM_PROMPT = """Ты пишешь посты для личного аккаунта в X (Twitter).

Правила, обязательные к исполнению:
- Ровно ОДИН пост, максимум {max_len} символов. Считай символы.
- Язык: {language}.
- Тема аккаунта: {topic}
- Тон: {tone}
- Никаких вступлений, кавычек, пояснений и вариантов на выбор — только текст поста.
- Не начинай со слов «Вот пост», «Сегодня я хочу рассказать» и подобной воды.
- Не выдумывай факты, цифры, цитаты и новости. Если не уверен — пиши мысль, наблюдение
  или вопрос, а не «статистику».
- Не используй больше одного эмодзи и больше двух хэштегов.
- Не повторяй по смыслу посты из списка «уже опубликовано»."""


def from_ai(
    api_key: str,
    model: str,
    topic: str,
    tone: str,
    language: str,
    history: History,
    attempts: int = 3,
) -> str:
    try:
        from anthropic import Anthropic
    except ImportError as e:
        raise GeneratorError("Не установлен пакет anthropic: pip install anthropic") from e

    client = Anthropic(api_key=api_key)

    recent = history.recent_texts(15)
    recent_block = "\n".join(f"- {t}" for t in recent) if recent else "(пока пусто)"

    system = SYSTEM_PROMPT.format(max_len=MAX_LEN, language=language, topic=topic, tone=tone)

    for attempt in range(1, attempts + 1):
        try:
            response = client.messages.create(
                model=model,
                max_tokens=400,
                system=system,
                messages=[
                    {
                        "role": "user",
                        "content": (
                            f"Уже опубликовано (не повторяйся):\n{recent_block}\n\n"
                            f"Напиши новый пост. Только текст поста, ничего больше."
                        ),
                    }
                ],
            )
        except Exception as e:  # сетевые ошибки, лимиты, невалидный ключ
            log.warning("Ошибка Claude API (%s), попытка %s/%s", e, attempt, attempts)
            continue

        text = "".join(
            block.text for block in response.content if getattr(block, "type", "") == "text"
        ).strip()
        text = text.strip('"').strip()

        if not text:
            log.warning("Пустой ответ модели, попытка %s/%s", attempt, attempts)
            continue
        if len(text) > MAX_LEN:
            log.warning("Сгенерирован пост на %s символов, повторяю", len(text))
            continue
        if history.is_duplicate(text):
            log.warning("Сгенерирован дубль, повторяю")
            continue

        return text

    raise GeneratorError(f"Не удалось сгенерировать валидный пост за {attempts} попыток")


# ------------------------------------------------------------------- диспетчер

def generate(cfg, history: History) -> str:
    if cfg.content_mode == "file":
        return from_file(cfg.posts_file, history)
    return from_ai(
        api_key=cfg.anthropic_api_key,
        model=cfg.model,
        topic=cfg.account_topic,
        tone=cfg.account_tone,
        language=cfg.post_language,
        history=history,
    )
