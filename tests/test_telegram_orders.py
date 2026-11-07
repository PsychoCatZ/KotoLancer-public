import pytest
from sqlalchemy import func, select

from kotolancer.db import make_engine, session_factory
from kotolancer.models import AIJob, Material, Order, OrderEvent, ProfileHead, TelegramUpdate
from kotolancer.telegram_bot import TelegramError, ingest_next, persist_batch
from tests.test_integrations import BotFixture, update


class LinkedBot(BotFixture):
    fail_send = False

    def call(self, method, **body):
        self.replies.append((method, body))
        if self.fail_send:
            raise TelegramError("Unknown delivery")
        return {"message_id": 100 + len(self.replies)}


@pytest.fixture
def bot_context(settings):
    settings.telegram_owner_id = 42
    settings.mini_app_url = "https://example.test/?keep=1"
    engine = make_engine(settings.database_url)
    factory = session_factory(engine)
    yield settings, factory, LinkedBot()
    engine.dispose()


def send(context, payload):
    settings, factory, bot = context
    persist_batch(factory, 123, [payload], 42)
    while ingest_next(factory, settings, bot, 123):
        pass


def reply(message_id, **changes):
    return {"message_id": message_id, "from": {"id": 123, "is_bot": True}, "chat": {"id": 42}, **changes}


def test_new_reply_restart_and_unrelated_inbox(bot_context):
    settings, factory, bot = bot_context
    payload = update(text="/new Бот записи")
    send(bot_context, payload)
    send(bot_context, payload)
    assert len(bot.replies) == 1
    buttons = bot.replies[0][1]["reply_markup"]
    with factory() as db:
        order = db.scalar(select(Order))
        order_id = order.id
        assert order.status == "new" and order.accepted_at is None
        assert db.scalar(select(func.count()).select_from(Order)) == 1
        assert "keep=1" in buttons["inline_keyboard"][0][0]["web_app"]["url"]
        assert "order=" + order_id in buttons["inline_keyboard"][0][0]["web_app"]["url"]
    # New session factory: destination survives process restart; reply text is not a routing key.
    restarted = settings, session_factory(make_engine(settings.database_url)), bot
    send(restarted, update(2, text="ТЗ", reply_to_message=reply(101, text="Любой текст")))
    send(restarted, update(3, photo=[{"file_id": "photo"}], reply_to_message=reply(102)))
    send(restarted, update(4, text="Другое объявление без ответа"))
    with factory() as db:
        assert db.scalar(select(Order).where(Order.id == order_id)).materials_revision == 2
        materials = list(db.scalars(select(Material)))
        assert sum(m.order_id == order_id for m in materials) == 2
        assert sum(m.order_id is None for m in materials) == 1
        assert db.scalar(select(func.count()).select_from(AIJob)) == 0
        assert db.get(ProfileHead, 1).revision_id == 1
    restarted[1].kw["bind"].dispose()


def test_two_cards_use_only_exact_bot_message_link(bot_context):
    _, factory, _ = bot_context
    send(bot_context, update(text="/new Первый"))
    send(bot_context, update(2, text="/new Второй"))
    send(bot_context, update(3, text="К первому", reply_to_message=reply(101)))
    send(bot_context, update(4, text="Ко второму", reply_to_message=reply(102)))
    send(bot_context, update(5, text="Нет связи", reply_to_message=reply(999)))
    send(
        bot_context,
        update(6, text="Чужой бот", reply_to_message=reply(101, **{"from": {"id": 900, "is_bot": True}})),
    )
    send(bot_context, update(7, owner=43, text="Не Кот", reply_to_message=reply(101)))
    with factory() as db:
        names = {o.id: o.title for o in db.scalars(select(Order))}
        materials = {m.text: names.get(m.order_id) for m in db.scalars(select(Material))}
        assert materials == {
            "К первому": "Первый",
            "Ко второму": "Второй",
            "Нет связи": None,
            "Чужой бот": None,
        }


def test_unknown_send_is_not_retried_and_reply_falls_back_to_inbox(bot_context):
    _, factory, bot = bot_context
    bot.fail_send = True
    payload = update(text="/new Не потеряется")
    send(bot_context, payload)
    send(bot_context, payload)
    assert len(bot.replies) == 1
    bot.fail_send = False
    send(bot_context, update(2, text="Ответ без известной квитанции", reply_to_message=reply(101)))
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Order)) == 1
        assert db.scalar(select(Material)).order_id is None
        assert not db.scalar(select(OrderEvent).where(OrderEvent.kind == "telegram_card"))


def test_forwarded_commands_are_material_and_bad_commands_do_not_create_orders(bot_context):
    _, factory, _ = bot_context
    send(bot_context, update(text="/new Это пересылка", forward_origin={"type": "user"}))
    for i, text in enumerate(["/new", "/new " + "x" * 201, "/order nope", "/order " + "0" * 36], 2):
        send(bot_context, update(i, text=text))
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Order)) == 0
        assert db.scalar(select(Material)).text == "/new Это пересылка"


def test_existing_order_card_and_accepted_order_stays_accepted(bot_context):
    _, factory, _ = bot_context
    from kotolancer.models import now

    with factory() as db:
        order = Order(title="Уже в работе", status="in_progress", accepted_at=now())
        db.add(order)
        db.commit()
        order_id = order.id
    send(bot_context, update(text="/order " + order_id))
    send(bot_context, update(2, text="Дополнительный материал", reply_to_message=reply(101)))
    with factory() as db:
        order = db.get(Order, order_id)
        assert order.status == "in_progress" and order.accepted_at is not None
        assert db.scalar(select(Material)).order_id == order_id
        assert db.scalar(select(func.count()).select_from(AIJob)) == 0


def test_attachment_error_does_not_change_order_or_link_file(bot_context):
    _, factory, _ = bot_context
    send(bot_context, update(text="/new Тест"))
    send(
        bot_context,
        update(2, document={"file_id": "file", "file_name": "unsafe.html"}, reply_to_message=reply(101)),
    )
    with factory() as db:
        assert db.scalar(select(Order)).materials_revision == 0
        assert db.scalar(select(func.count()).select_from(Material)) == 0
        assert db.get(TelegramUpdate, (123, 2)).status == "failed"


def test_reply_link_supports_64_bit_telegram_ids(settings):
    # PostgreSQL JSON.as_integer() casts to INTEGER and fails above 2**31-1.
    # SQLite alone does not expose this overflow; CI also runs this on PostgreSQL.
    from kotolancer.telegram_orders import remember_card

    owner_id, bot_id, message_id = 7_000_000_042, 8_000_000_123, 9_000_000_101
    settings.telegram_owner_id = owner_id
    engine = make_engine(settings.database_url)
    factory = session_factory(engine)
    bot = LinkedBot()
    with factory() as db:
        order = Order(title="64-bit Telegram IDs")
        db.add(order)
        db.commit()
        order_id = order.id
    remember_card(factory, {"message_id": message_id}, order_id, bot_id, owner_id)
    payload = update(
        10,
        owner=owner_id,
        text="Материал с большими ID",
        reply_to_message={
            "message_id": message_id,
            "from": {"id": bot_id, "is_bot": True},
            "chat": {"id": owner_id},
        },
    )
    persist_batch(factory, bot_id, [payload], owner_id)
    assert ingest_next(factory, settings, bot, bot_id)
    assert not ingest_next(factory, settings, bot, bot_id)
    with factory() as db:
        material = db.scalar(select(Material))
        assert material.order_id == order_id
        assert db.get(Order, order_id).materials_revision == 1
        assert db.get(TelegramUpdate, (bot_id, 10)).status == "saved"
        assert db.scalar(select(func.count()).select_from(AIJob)) == 0
    engine.dispose()
