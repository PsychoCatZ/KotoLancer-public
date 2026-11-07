"""Explicit isolated browser QA. Uses fixture answers, never calls OpenAI."""

import json
import threading
from contextlib import asynccontextmanager
from uuid import uuid4

import psycopg
import uvicorn
from psycopg import sql
from sqlalchemy.engine import make_url

from kotolancer.app import create_app
from kotolancer.config import ROOT, Settings
from kotolancer.consultant import process_next
from kotolancer.db import make_engine, session_factory
from kotolancer.manage import migrate
from kotolancer.models import AIWorkerState, Order, now
from kotolancer.profile import initialize_profile
from tests.test_consultant import FixtureProvider


def main():
    original = Settings()
    if not original.database_url.startswith("postgresql+psycopg://"):
        raise RuntimeError("UI preview requires a local PostgreSQL database.")
    schema = f"ui_preview_{uuid4().hex}"
    admin = psycopg.connect(
        original.database_url.replace("postgresql+psycopg://", "postgresql://"), autocommit=True
    )
    admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    url = (
        make_url(original.database_url)
        .update_query_dict({"options": f"-csearch_path={schema}"})
        .render_as_string(hide_password=False)
    )
    settings = Settings(
        _env_file=None,
        database_url=url,
        storage_dir=ROOT / "artifacts" / "ui-preview-files",
        openai_model="fixture-only",
    )
    migrate(url)
    engine = make_engine(url)
    factory = session_factory(engine)
    with factory() as db:
        initialize_profile(db)
        db.add(
            Order(
                title="ПРОВЕРКА ИНТЕРФЕЙСА: бот для входящих заявок",
                source="Тестовые данные",
                description="Проверяем интерфейс на заранее заданных ответах. Реальная модель не вызывается.",
            )
        )
        db.commit()
    marker = ROOT / "artifacts" / "ui-preview.json"
    marker.write_text(json.dumps({"schema": schema, "port": 8011}), encoding="utf-8")
    provider = FixtureProvider()
    app = create_app(settings, ai_provider=provider)
    base_lifespan = app.router.lifespan_context
    stop = threading.Event()

    def work():
        while not stop.is_set():
            with factory() as db:
                heartbeat = db.get(AIWorkerState, 1)
                if heartbeat:
                    heartbeat.heartbeat_at = now()
                else:
                    db.add(AIWorkerState(id=1))
                db.commit()
            if not process_next(factory, settings, provider):
                stop.wait(0.25)

    @asynccontextmanager
    async def lifespan(application):
        async with base_lifespan(application):
            thread = threading.Thread(target=work, daemon=True)
            thread.start()
            try:
                yield
            finally:
                stop.set()
                thread.join(timeout=3)
                engine.dispose()

    app.router.lifespan_context = lifespan
    try:
        uvicorn.run(app, host="127.0.0.1", port=8011, proxy_headers=False)
    finally:
        engine.dispose()
        admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
        admin.close()


if __name__ == "__main__":
    main()
