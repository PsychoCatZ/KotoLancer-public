"""Container-local probes. No public auth bypass and no external API requests."""

import argparse
from datetime import UTC

import httpx
from sqlalchemy import text

from .config import Settings
from .db import make_engine, session_factory
from .models import AIWorkerState, TelegramCursor, now


def recent(stamp, seconds):
    if stamp is None:
        return False
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return -30 <= (now() - stamp).total_seconds() <= seconds


def check(kind, settings, client=None):
    if kind == "api":
        owned = client is None
        client = client or httpx.Client(timeout=5, trust_env=False)
        try:
            response = client.get("http://127.0.0.1:8000/")
            if response.status_code != 200:
                raise ValueError("API shell unavailable")
        finally:
            if owned:
                client.close()
    engine = make_engine(settings.database_url)
    try:
        with session_factory(engine)() as db:
            db.execute(text("SELECT 1"))
            if kind == "telegram":
                prefix = settings.telegram_bot_token.get_secret_value().partition(":")[0]
                row = db.get(TelegramCursor, int(prefix)) if prefix.isdecimal() else None
                if not row or not recent(row.last_poll_at, 90):
                    raise ValueError("Telegram polling stale")
            if kind == "worker":
                row = db.get(AIWorkerState, 1)
                if (
                    not settings.ai_configured
                    or not row
                    or not recent(row.heartbeat_at, settings.openai_timeout_seconds * 2 + 90)
                ):
                    raise ValueError("AI worker heartbeat stale")
    finally:
        engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=["api", "telegram", "worker"])
    args = parser.parse_args()
    try:
        check(args.kind, Settings())
    except Exception:
        # Never print connection URLs, request objects or environment on failure.
        print(f"{args.kind}: unhealthy")
        raise SystemExit(1) from None
    print(f"{args.kind}: healthy")


if __name__ == "__main__":
    main()
