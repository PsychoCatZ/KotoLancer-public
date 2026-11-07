import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl


def verify_init_data(raw: str, token: str, owner_id: int, max_age: int, *, timestamp=None):
    """Validate original Telegram initData, never initDataUnsafe or a supplied user ID."""
    if not raw or len(raw) > 16384 or not token or not owner_id:
        raise ValueError("Открой Mini App из своего Telegram-бота.")
    try:
        pairs = parse_qsl(
            raw, keep_blank_values=True, strict_parsing=True, max_num_fields=40, errors="strict"
        )
        fields = dict(pairs)
        if len(fields) != len(pairs):
            raise ValueError("Duplicate fields")
        signature = fields.pop("hash")
        secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
        expected = hmac.new(
            secret, "\n".join(f"{k}={v}" for k, v in sorted(fields.items())).encode(), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError("Bad signature")
        instant = int(time.time() if timestamp is None else timestamp)
        age = instant - int(fields["auth_date"])
        user = json.loads(fields["user"])
        if age < -30 or age > max_age or type(user.get("id")) is not int or user["id"] != owner_id:
            raise ValueError("Expired or wrong owner")
    except (ValueError, KeyError, TypeError, UnicodeError, AttributeError) as exc:
        raise ValueError("Доступ только Коту. Перезапусти Mini App из своего бота.") from exc
    return owner_id
