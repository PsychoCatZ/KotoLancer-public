"""Private, owner-only Telegram approval for the browser's independent challenge."""

import re

from .browser_auth import login_by_approval


def authorized_callback(payload, owner_id, bot_id):
    callback = payload.get("callback_query") or {}
    message = callback.get("message") or {}
    return (
        callback
        if (
            callback.get("from", {}).get("id") == owner_id
            and not callback.get("from", {}).get("is_bot")
            and message.get("chat", {}).get("id") == owner_id
            and message.get("chat", {}).get("type") == "private"
            and message.get("from", {}).get("id") == bot_id
        )
        else None
    )


def browser_command(db, payload, settings, bot_id):
    callback = authorized_callback(payload, settings.telegram_owner_id, bot_id)
    text = (payload.get("message") or {}).get("text", "")
    start = re.fullmatch(r"/start(?:@[A-Za-z0-9_]+)? login_([A-Za-z0-9_-]{43})", text)
    action = (
        re.fullmatch(r"login_(ok|no):([A-Za-z0-9_-]{43})", callback.get("data", "")) if callback else None
    )
    if not start and not action:
        return None
    token = start[1] if start else action[2]
    row = login_by_approval(db, token, settings)
    callback_id = callback.get("id") if callback else None
    if not row or row.status not in {"pending", "approved"}:
        return ("Запрос входа истёк или уже закрыт. Начни новый на сайте мастерской.", None, callback_id)
    if action:
        if row.status == "pending":
            row.status = "approved" if action[1] == "ok" else "rejected"
        reply = (
            "Вход подтверждён. Вернись в тот браузер, где начал вход."
            if row.status == "approved"
            else "Вход отклонён. Браузер не получит доступ."
        )
        return reply, None, callback_id
    buttons = {
        "inline_keyboard": [
            [
                {"text": "Это я, войти", "callback_data": "login_ok:" + token},
                {"text": "Не я — отклонить", "callback_data": "login_no:" + token},
            ]
        ]
    }
    return (
        f"Кот, вход в браузере. Код: {row.code}.\nСверь его с кодом на сайте мастерской. Подтверждай только вход, который сам начал. Запрос действует пять минут.",
        buttons,
        None,
    )
