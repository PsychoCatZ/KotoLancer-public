"""Explicit owner commands and durable bot-message links. No implicit active order."""

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import BigInteger, cast, select

from .models import Order, OrderEvent


def order_command(db, message):
    # Forwarded announcements are source material, even when they look like commands.
    if message.get("forward_origin") or message.get("forward_date"):
        return None
    text = message.get("text", "")
    match = re.fullmatch(r"/(new|order)(?:@[A-Za-z0-9_]+)?(?:\s+([\s\S]*))?", text)
    if not match:
        return None
    command, value = match[1], (match[2] or "").strip()
    if command == "new":
        if not value or len(value) > 200:
            return None, "Кот, напиши /new и название заказа — от 1 до 200 символов."
        order = Order(title=value)
        db.add(order)
        db.flush()
        db.add(OrderEvent(order_id=order.id, kind="created", detail={"source": "telegram_command"}))
        return order, card_text(order, created=True)
    if not re.fullmatch(r"[a-f0-9-]{36}", value):
        return None, "Для существующего заказа: /order и его ID из карточки мастерской."
    order = db.get(Order, value)
    if order and (order.deleted_at or order.archived_at):
        return None, "Заказ в архиве или корзине. Сначала восстанови его в мастерской."
    return (order, card_text(order)) if order else (None, "Такого заказа не нашёл. Проверь ID в мастерской.")


def card_text(order, *, created=False):
    return (
        ("Создал карточку" if created else "Карточка заказа") + f": {order.title}\nID: {order.id}\n"
        "Отвечай на это сообщение текстом, скриншотом, PDF или DOCX — прикреплю к этому заказу. "
        "Обычные пересылки без ответа сохраняю во Входящие. Анализ запускается в мастерской отдельно."
    )


def reply_order(db, message, bot_id, owner_id):
    reply = message.get("reply_to_message") or {}
    if (
        reply.get("from", {}).get("id") != bot_id
        or not reply.get("from", {}).get("is_bot")
        or reply.get("chat", {}).get("id") != owner_id
        or not isinstance(reply.get("message_id"), int)
    ):
        return None
    order_id = db.scalar(
        select(OrderEvent.order_id).where(
            OrderEvent.kind == "telegram_card",
            cast(OrderEvent.detail["bot_id"].as_string(), BigInteger) == bot_id,
            cast(OrderEvent.detail["chat_id"].as_string(), BigInteger) == owner_id,
            cast(OrderEvent.detail["message_id"].as_string(), BigInteger) == reply["message_id"],
        )
    )
    return (
        db.scalar(
            select(Order)
            .where(Order.id == order_id, Order.deleted_at.is_(None), Order.archived_at.is_(None))
            .with_for_update()
        )
        if order_id
        else None
    )


def card_buttons(settings, order_id):
    if not settings.mini_app_url:
        return None
    url = urlsplit(settings.mini_app_url)
    query = [(k, v) for k, v in parse_qsl(url.query) if k != "order"]
    query.append(("order", order_id))
    target = urlunsplit(url._replace(query=urlencode(query)))
    return {"inline_keyboard": [[{"text": "Открыть карточку", "web_app": {"url": target}}]]}


def remember_card(factory, response, order_id, bot_id, owner_id):
    # Unknown send/commit outcome is not retried. An unlinked reply remains in the inbox.
    if not isinstance(response, dict) or not isinstance(response.get("message_id"), int):
        return
    with factory() as db:
        if db.get(Order, order_id):
            db.add(
                OrderEvent(
                    order_id=order_id,
                    kind="telegram_card",
                    detail={"bot_id": bot_id, "chat_id": owner_id, "message_id": response["message_id"]},
                )
            )
            db.commit()
