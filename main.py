#!/usr/bin/env python3
"""
Автопостинг в X (Twitter).

Режимы:
  python main.py once      — опубликовать один пост и выйти (для cron / GitHub Actions)
  python main.py run       — демон: сам ждёт нужное время и постит по расписанию
  python main.py preview   — сгенерировать текст и показать, НЕ публикуя
  python main.py check     — проверить, что ключи рабочие
"""

import argparse
import logging
import random
import sys
import time
from datetime import date, datetime, timedelta
from datetime import time as dt_time

from config import load_config
from generator import GeneratorError, generate
from history import History
from poster import Poster, PostError

log = logging.getLogger("autopost")

# Результаты post_once. Важно различать «сегодня уже хватит» и настоящую поломку:
# от этого зависит код возврата, а значит — красный или зелёный шаг в CI.
POSTED = "posted"
SKIPPED = "skipped"
FAILED = "failed"


def setup_logging(log_file) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)


# ------------------------------------------------------------------ расписание

def pick_hours(hours: list[int], count: int) -> list[int]:
    """Выбираем count часов, распределяя их по дню, а не срезая первые подряд.

    Иначе при POSTS_PER_DAY=2 и ACTIVE_HOURS=10,15,20 вечерний слот
    не использовался бы никогда.
    """
    hours = sorted(set(hours))
    if count >= len(hours):
        return hours
    if count == 1:
        return [hours[0]]
    step = (len(hours) - 1) / (count - 1)
    return [hours[round(i * step)] for i in range(count)]


def slot_times(cfg, day: date) -> list[datetime]:
    """Времена публикаций на конкретный день, со стабильным случайным сдвигом."""
    hours = pick_hours(cfg.active_hours, cfg.posts_per_day)
    times = []
    for hour in hours:
        # seed привязан к дню и часу → сдвиг не «прыгает» при пересчёте
        rnd = random.Random(f"{day.isoformat()}:{hour}")
        offset = rnd.randint(-cfg.jitter_minutes, cfg.jitter_minutes)
        base = datetime.combine(day, dt_time(hour=hour))
        times.append(base + timedelta(minutes=offset))
    return sorted(times)


def next_slot(cfg, now: datetime) -> datetime:
    for day in (now.date(), now.date() + timedelta(days=1), now.date() + timedelta(days=2)):
        for moment in slot_times(cfg, day):
            if moment > now:
                return moment
    raise RuntimeError("Не удалось вычислить следующее время публикации")


def sleep_until(moment: datetime) -> None:
    """Спим короткими отрезками — так Ctrl+C работает мгновенно."""
    while True:
        remaining = (moment - datetime.now()).total_seconds()
        if remaining <= 0:
            return
        time.sleep(min(remaining, 60))


# -------------------------------------------------------------------- действия

def post_once(cfg) -> str:
    history = History(cfg.history_file)

    already = history.posted_today()
    if already >= cfg.max_posts_per_day:
        log.warning("Сегодня уже %s постов (лимит %s). Пропускаю.", already, cfg.max_posts_per_day)
        return SKIPPED

    try:
        text = generate(cfg, history)
    except GeneratorError as e:
        log.error("Не смог получить текст поста: %s", e)
        return FAILED

    log.info("Текст поста (%s символов):\n%s", len(text), text)

    poster = Poster(
        cfg.x_api_key, cfg.x_api_secret,
        cfg.x_access_token, cfg.x_access_secret,
        dry_run=cfg.dry_run,
    )

    try:
        tweet_id = poster.post(text)
    except PostError as e:
        log.error("Публикация не удалась: %s", e)
        return FAILED

    if cfg.dry_run:
        # В истории только реально опубликованное: иначе холостые прогоны
        # съедали бы суточный лимит и «сжигали» посты из пула.
        log.info("[DRY RUN] В историю не записываю, суточный лимит не расходуется.")
        return POSTED

    history.add(text, tweet_id)
    return POSTED


def daemon(cfg) -> None:
    log.info(
        "Демон запущен. Постов в день: %s, часы: %s, разброс ±%s мин, режим контента: %s%s",
        cfg.posts_per_day, cfg.active_hours, cfg.jitter_minutes, cfg.content_mode,
        " (DRY RUN)" if cfg.dry_run else "",
    )
    while True:
        try:
            # Расчёт слота тоже внутри try: раньше исключение отсюда
            # убивало весь демон, а не одну итерацию.
            moment = next_slot(cfg, datetime.now())
            log.info("Следующий пост: %s", moment.strftime("%Y-%m-%d %H:%M"))
            sleep_until(moment)
            post_once(cfg)
        except KeyboardInterrupt:
            log.info("Остановлен вручную.")
            return
        except Exception:
            # Демон не должен падать из-за одной ошибки — логируем и живём дальше
            log.exception("Непредвиденная ошибка в цикле, продолжаю работу")
            time.sleep(60)


def preview(cfg) -> None:
    history = History(cfg.history_file)
    text = generate(cfg, history)
    print(f"\n--- превью, {len(text)}/280 символов ---\n{text}\n")


def check(cfg) -> None:
    import tweepy

    client = tweepy.Client(
        consumer_key=cfg.x_api_key,
        consumer_secret=cfg.x_api_secret,
        access_token=cfg.x_access_token,
        access_token_secret=cfg.x_access_secret,
    )
    me = client.get_me()
    print(f"Ключи рабочие. Аккаунт: @{me.data.username} (id {me.data.id})")
    print("Расписание на сегодня:", [t.strftime("%H:%M") for t in slot_times(cfg, date.today())])


# ------------------------------------------------------------------------ CLI

def main() -> int:
    parser = argparse.ArgumentParser(description="Автопостинг в X (Twitter)")
    parser.add_argument("command", choices=["once", "run", "preview", "check"])
    args = parser.parse_args()

    try:
        cfg = load_config()
    except RuntimeError as e:
        print(f"Ошибка конфигурации: {e}", file=sys.stderr)
        return 1

    setup_logging(cfg.log_file)

    try:
        if args.command == "once":
            # Пропуск по суточному лимиту — штатная ситуация, не ошибка,
            # иначе шаг в GitHub Actions краснеет на ровном месте.
            return 1 if post_once(cfg) == FAILED else 0
        if args.command == "run":
            daemon(cfg)
        elif args.command == "preview":
            preview(cfg)
        elif args.command == "check":
            check(cfg)
    except KeyboardInterrupt:
        print("\nОстановлено.")
        return 130
    except Exception as e:
        log.exception("Фатальная ошибка: %s", e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
