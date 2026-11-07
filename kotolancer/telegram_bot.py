"""Owner-only durable Telegram inbox. No model calls and no automatic order acceptance."""

import logging
import time
from urllib.parse import quote

import httpx
from sqlalchemy import select

from .browser_bot import authorized_callback, browser_command
from .config import Settings
from .db import make_engine, session_factory
from .materials import safe_filename, save_upload
from .models import Material, MaterialExtract, OrderEvent, TelegramCursor, TelegramUpdate, now
from .telegram_orders import card_buttons, order_command, remember_card, reply_order


class TelegramError(Exception):
    pass


class TelegramClient:
    def __init__(self, settings, client=None):
        self.settings = settings
        self.token = settings.telegram_bot_token.get_secret_value()
        self.client = client or httpx.Client(timeout=40, follow_redirects=False)

    def call(self, method, **body):
        if method not in {
            "getMe",
            "getWebhookInfo",
            "getUpdates",
            "getFile",
            "sendMessage",
            "answerCallbackQuery",
        }:
            raise TelegramError("Недопустимый метод Telegram.")
        try:
            response = self.client.post(f"https://api.telegram.org/bot{self.token}/{method}", json=body)
            response.raise_for_status()
            result = response.json()
            if not result.get("ok"):
                raise ValueError("Telegram rejected request")
            return result["result"]
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            raise TelegramError("Telegram не выполнил запрос. Проверь доступ; токен не публикуется.") from exc

    def download(self, file_id):
        info = self.call("getFile", file_id=file_id)
        path = info.get("file_path", "")
        if (
            not path
            or path.startswith("/")
            or any(p in {".", ".."} for p in path.split("/"))
            or info.get("file_size", 0) > self.settings.max_upload_bytes
        ):
            raise TelegramError("Файл Telegram недоступен или превышает лимит.")
        try:
            with self.client.stream(
                "GET", f"https://api.telegram.org/file/bot{self.token}/{quote(path, safe='/')}"
            ) as response:
                response.raise_for_status()
                data = bytearray()
                for chunk in response.iter_bytes():
                    data.extend(chunk)
                    if len(data) > self.settings.max_upload_bytes:
                        raise TelegramError("Файл превышает лимит 20 МБ.")
                return bytes(data)
        except httpx.HTTPError as exc:
            raise TelegramError("Не удалось скачать вложение Telegram.") from exc


def authorized_message(payload, owner_id):
    message = payload.get("message") or {}
    return (
        message
        if (
            message.get("chat", {}).get("type") == "private"
            and message.get("chat", {}).get("id") == owner_id
            and message.get("from", {}).get("id") == owner_id
            and not message.get("from", {}).get("is_bot")
        )
        else None
    )


def persist_batch(factory, bot_id, updates, owner_id):
    """Durably store a complete batch before confirming its next offset to Telegram."""
    with factory() as db:
        cursor = db.scalar(select(TelegramCursor).where(TelegramCursor.bot_id == bot_id).with_for_update())
        if not cursor:
            cursor = TelegramCursor(bot_id=bot_id, next_offset=0)
            db.add(cursor)
            db.flush()
        for payload in updates:
            update_id = payload["update_id"]
            if not db.get(TelegramUpdate, (bot_id, update_id)):
                allowed = authorized_message(payload, owner_id) or authorized_callback(
                    payload, owner_id, bot_id
                )
                db.add(
                    TelegramUpdate(
                        bot_id=bot_id,
                        update_id=update_id,
                        payload=payload if allowed else {},
                        status="pending" if allowed else "ignored",
                    )
                )
            cursor.next_offset = max(cursor.next_offset, update_id + 1)
        cursor.last_poll_at = now()
        db.commit()


def ingest_next(factory, settings, client, bot_id):
    path = None
    commit_attempted = False
    with factory() as db:
        row = db.scalar(
            select(TelegramUpdate)
            .where(TelegramUpdate.bot_id == bot_id, TelegramUpdate.status == "pending")
            .order_by(TelegramUpdate.update_id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if not row:
            return False
        message = authorized_message(row.payload, settings.telegram_owner_id)
        callback = authorized_callback(row.payload, settings.telegram_owner_id, bot_id)
        if not message and not callback:
            row.status = "ignored"
            commit_attempted = True
            db.commit()
            return True
        browser_reply = None
        command_reply = None
        linked_order = None
        try:
            browser_reply = browser_command(db, row.payload, settings, bot_id)
            message = message or {}
            text = message.get("text") or message.get("caption") or ""
            if browser_reply:
                row.status = "command"
                row.payload = {}  # Short-lived approval capabilities must not remain in the durable inbox.
            elif callback:
                row.status = "ignored"
                row.payload = {}
            elif (
                not message.get("document")
                and not message.get("photo")
                and (command_reply := order_command(db, message))
            ):
                row.status = "command"
                linked_order = command_reply[0]
            elif (
                text.startswith("/")
                and not message.get("forward_origin")
                and not message.get("forward_date")
                and not message.get("document")
                and not message.get("photo")
            ):
                row.status = "command"
            else:
                file = message.get("document")
                if not file and message.get("photo"):
                    file = {**message["photo"][-1], "file_name": f"telegram-{message['message_id']}.jpg"}
                if file:
                    if file.get("file_size", 0) > settings.max_upload_bytes:
                        raise ValueError("Вложение больше 20 МБ. Раздели его на части.")
                    fields, extraction = save_upload(
                        client.download(file["file_id"]), file.get("file_name", "file"), settings
                    )
                    path = settings.storage_dir / fields["file_key"]
                    material = Material(**fields, text=text)
                    db.add(material)
                    db.flush()
                    db.add(
                        MaterialExtract(
                            material_id=material.id,
                            blocks=extraction["blocks"],
                            warnings=extraction["warnings"],
                        )
                    )
                elif text.strip():
                    material = Material(
                        title=safe_filename(text[:100]), text=text, kind="text", extraction_status="ready"
                    )
                    db.add(material)
                    db.flush()
                else:
                    raise ValueError("Пришли текст, скриншот, PDF или DOCX.")
                row.material_ids = [material.id]
                linked_order = reply_order(db, message, bot_id, settings.telegram_owner_id)
                if linked_order:
                    material.order_id = linked_order.id
                    linked_order.materials_revision += 1
                    linked_order.updated_at = now()
                    db.add(
                        OrderEvent(
                            order_id=linked_order.id,
                            kind="material_attached",
                            detail={
                                "material_id": material.id,
                                "title": material.title,
                                "source": "telegram_reply",
                            },
                        )
                    )
                row.status = "saved"
            commit_attempted = True
            db.commit()
        except (ValueError, TelegramError) as exc:
            if path and not commit_attempted:
                path.unlink(missing_ok=True)
            row.status, row.error = "failed", str(exc)
            db.commit()
        except Exception:
            if path and not commit_attempted:
                path.unlink(missing_ok=True)
            db.rollback()
            raise
        # Reply happens after durable saving. Unknown reply delivery is never retried automatically.
        reply = row.error or (
            "Привет, Кот! Пересылай объявления текстом, скриншотами, PDF или DOCX — сохраню во Входящие. "
            "Для новой карточки: /new и название. Ответом на карточку можно прикрепить материалы именно к ней."
            if row.status == "command"
            else "Сохранил во Входящие KotoLancer. Открой мастерскую и прикрепи материал к заказу."
        )
        buttons = (
            {"inline_keyboard": [[{"text": "Открыть KotoLancer", "web_app": {"url": settings.mini_app_url}}]]}
            if settings.mini_app_url
            else None
        )
        callback_id = None
        link_id = None
        if browser_reply:
            reply, buttons, callback_id = browser_reply
        elif callback:
            return True
        elif not row.error and (command_reply or linked_order):
            if command_reply:
                reply = command_reply[1]
            else:
                reply = (
                    f"Прикрепил материал к заказу «{linked_order.title}». Анализ автоматически не запускал."
                )
            if linked_order:
                link_id = linked_order.id
                buttons = card_buttons(settings, link_id)
    try:
        if callback_id:
            client.call("answerCallbackQuery", callback_query_id=callback_id, text="Готово")
        response = client.call(
            "sendMessage",
            chat_id=settings.telegram_owner_id,
            text=reply,
            **({"reply_markup": buttons} if buttons else {}),
        )
        if link_id:
            remember_card(factory, response, link_id, bot_id, settings.telegram_owner_id)
    except TelegramError:
        pass
    return True


def main():
    settings = Settings()
    if not settings.telegram_owner_id or not settings.telegram_bot_token.get_secret_value():
        print("Telegram disabled: configure the dedicated bot token and owner ID.")
        return
    # Tokens form part of Bot API URLs. Suppress transport URL logs.
    logging.getLogger("httpx").setLevel(logging.CRITICAL)
    logging.getLogger("httpcore").setLevel(logging.CRITICAL)
    engine = make_engine(settings.database_url)
    factory = session_factory(engine)
    client = TelegramClient(settings)
    try:
        bot = client.call("getMe")
        if client.call("getWebhookInfo").get("url"):
            raise TelegramError(
                "У бота настроен webhook. Он не изменён; используй отдельного бота KotoLancer."
            )
        if bot["username"].lower() != settings.telegram_bot_username.lower():
            raise TelegramError("Имя бота не совпадает с настройками браузерного входа.")
        print(f"Telegram inbox: @{bot['username']}; owner-only; no AI calls", flush=True)
        while True:
            try:
                while ingest_next(factory, settings, client, bot["id"]):
                    pass
                with factory() as db:
                    cursor = db.get(TelegramCursor, bot["id"])
                    offset = cursor.next_offset if cursor else 0
                updates = client.call(
                    "getUpdates", offset=offset, timeout=20, allowed_updates=["message", "callback_query"]
                )
                persist_batch(factory, bot["id"], updates, settings.telegram_owner_id)
            except TelegramError as exc:
                print(str(exc), flush=True)
                time.sleep(3)
            except Exception:
                print(
                    "Ошибка хранилища Telegram. Сохранённая очередь не сбрасывается; приём попробует продолжить.",
                    flush=True,
                )
                time.sleep(3)
    except KeyboardInterrupt:
        pass
    except TelegramError as exc:
        print(str(exc), flush=True)
    finally:
        client.client.close()
        engine.dispose()


if __name__ == "__main__":
    main()
