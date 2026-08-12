"""
Обёртка над X API v2 (endpoint POST /2/tweets) через tweepy.
Аутентификация — OAuth 1.0a User Context: постим от имени владельца токенов.
"""

import logging
import time

import tweepy

log = logging.getLogger(__name__)

TWEET_LIMIT = 280  # для аккаунтов без X Premium


class PostError(Exception):
    """Публикация не удалась и повтор не поможет."""


class Poster:
    def __init__(self, api_key: str, api_secret: str, access_token: str, access_secret: str,
                 dry_run: bool = False):
        self.dry_run = dry_run
        self.client = tweepy.Client(
            consumer_key=api_key,
            consumer_secret=api_secret,
            access_token=access_token,
            access_token_secret=access_secret,
            wait_on_rate_limit=False,  # обрабатываем 429 сами, с логами
        )

    @staticmethod
    def fits(text: str) -> bool:
        return 0 < len(text) <= TWEET_LIMIT

    def post(self, text: str, max_retries: int = 3) -> str | None:
        """Публикует твит. Возвращает id поста или None в dry-run."""
        text = text.strip()

        if not self.fits(text):
            raise PostError(f"Длина твита {len(text)} символов, лимит {TWEET_LIMIT}")

        if self.dry_run:
            log.info("[DRY RUN] Твит НЕ отправлен. Текст:\n%s", text)
            return None

        delay = 30
        for attempt in range(1, max_retries + 1):
            try:
                response = self.client.create_tweet(text=text)
                tweet_id = str(response.data["id"])
                log.info("Опубликовано: https://x.com/i/web/status/%s", tweet_id)
                return tweet_id

            except tweepy.errors.TooManyRequests as e:
                # 429 — упёрлись в rate limit. Ждём и пробуем ещё раз.
                reset = e.response.headers.get("x-rate-limit-reset") if e.response else None
                wait = delay
                if reset:
                    try:
                        wait = max(delay, int(reset) - int(time.time()) + 5)
                    except ValueError:
                        pass
                log.warning("Rate limit (429). Жду %s сек. Попытка %s/%s", wait, attempt, max_retries)
                time.sleep(min(wait, 900))
                delay *= 2

            except tweepy.errors.Forbidden as e:
                # 403 — дубликат текста, нет прав на запись, или нарушены правила.
                raise PostError(
                    f"403 Forbidden: {e}. Частые причины: точный дубль недавнего твита; "
                    f"у приложения права Read вместо Read and Write; токены выпущены "
                    f"до смены прав (перевыпусти Access Token)."
                ) from e

            except tweepy.errors.Unauthorized as e:
                raise PostError(
                    f"401 Unauthorized: {e}. Ключи неверные или отозваны — проверь .env."
                ) from e

            except tweepy.errors.TweepyException as e:
                log.warning("Ошибка API (%s). Попытка %s/%s, жду %s сек.",
                            e, attempt, max_retries, delay)
                time.sleep(delay)
                delay *= 2

        raise PostError(f"Не удалось опубликовать после {max_retries} попыток")
