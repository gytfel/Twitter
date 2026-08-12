"""
Обёртка над X API v2 (endpoint POST /2/tweets) через tweepy.

Два способа авторизации, оба постят от имени владельца токенов:
  oauth1 — OAuth 1.0a User Context, подпись ключами. Токен не протухает.
  oauth2 — OAuth 2.0 User Context, access token в заголовке Bearer.
           Живёт ~2 часа, обновляется через auth.get_access_token().
"""

import logging
import time

import requests
import tweepy

log = logging.getLogger(__name__)

TWEET_LIMIT = 280  # для аккаунтов без X Premium


class PostError(Exception):
    """Публикация не удалась и повтор не поможет."""


def build_client(cfg) -> tuple[tweepy.Client, bool]:
    """Клиент X API и флаг user_auth для вызовов.

    В OAuth 2.0 access token передаётся как bearer_token, а user_auth=False —
    tweepy тогда шлёт его заголовком Authorization: Bearer вместо подписи OAuth 1.0a.
    """
    if cfg.auth_mode == "oauth2":
        from auth import get_access_token

        client = tweepy.Client(
            bearer_token=get_access_token(cfg),
            wait_on_rate_limit=False,
        )
        return client, False

    client = tweepy.Client(
        consumer_key=cfg.x_api_key,
        consumer_secret=cfg.x_api_secret,
        access_token=cfg.x_access_token,
        access_token_secret=cfg.x_access_secret,
        wait_on_rate_limit=False,  # обрабатываем 429 сами, с логами
    )
    return client, True


class Poster:
    def __init__(self, client, user_auth: bool = True, dry_run: bool = False):
        self.client = client
        self.user_auth = user_auth
        self.dry_run = dry_run

    @classmethod
    def from_config(cls, cfg) -> "Poster":
        if cfg.dry_run:
            # В холостом режиме за токеном не ходим: сеть тут ни к чему, а в OAuth 2.0
            # поход за токеном ещё и провернул бы ротацию refresh token впустую.
            return cls(None, user_auth=(cfg.auth_mode == "oauth1"), dry_run=True)

        client, user_auth = build_client(cfg)
        return cls(client, user_auth=user_auth, dry_run=False)

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
                response = self.client.create_tweet(text=text, user_auth=self.user_auth)
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
                    f"до смены прав (перевыпусти Access Token). При OAuth 2.0 — "
                    f"в токене нет scope tweet.write, нужна повторная авторизация."
                ) from e

            except tweepy.errors.Unauthorized as e:
                raise PostError(
                    f"401 Unauthorized: {e}. Ключи неверные или отозваны — проверь .env."
                ) from e

            except requests.exceptions.RequestException as e:
                # Сетевые сбои tweepy в свои исключения не заворачивает, они летят
                # наружу как есть. Без этой ветки обрыв связи убивал бы публикацию
                # мимо ретраев — хотя ретраи нужны ровно для таких случаев.
                log.warning("Сеть недоступна (%s). Попытка %s/%s, жду %s сек.",
                            e, attempt, max_retries, delay)
                time.sleep(delay)
                delay *= 2

            except tweepy.errors.TweepyException as e:
                log.warning("Ошибка API (%s). Попытка %s/%s, жду %s сек.",
                            e, attempt, max_retries, delay)
                time.sleep(delay)
                delay *= 2

        raise PostError(f"Не удалось опубликовать после {max_retries} попыток")
