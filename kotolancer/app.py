import secrets
from contextlib import asynccontextmanager
from datetime import UTC
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from .ai_routes import consultant_routes
from .browser_auth import browser_routes, origin_guard, session_valid
from .config import ROOT, Settings
from .db import make_engine, session_factory
from .github_jobs import start_reader
from .github_review_routes import github_review_routes
from .integration_routes import integration_routes
from .materials import safe_filename, save_upload
from .models import AIJob, Material, MaterialExtract, Order, OrderEvent, ProfileRevision, TelegramCursor, now
from .order_recommendations import recommendation_bucket, recommendation_summaries
from .profile import ConflictError, current_profile, revise_profile, revision_dict, revision_history
from .profile_routes import profile_routes
from .schemas import (
    AttachMaterial,
    OrderFields,
    OrderUpdate,
    ProfileUpdate,
    StatusUpdate,
    TextMaterial,
    VisibilityUpdate,
)
from .telegram_auth import verify_init_data
from .work_routes import work_routes


def stamp(value):
    return value.replace(tzinfo=UTC).isoformat() if value and value.tzinfo is None else value


def order_dict(order: Order, count: int = 0):
    return {
        key: stamp(getattr(order, key)) if key.endswith("_at") else getattr(order, key)
        for key in (
            "id",
            "title",
            "source",
            "source_url",
            "budget",
            "description",
            "notes",
            "status",
            "version",
            "materials_revision",
            "conversation_revision",
            "accepted_at",
            "archived_at",
            "deleted_at",
            "created_at",
            "updated_at",
        )
    } | {"material_count": count, "consulting_allowed": order.accepted_at is None}


def create_app(settings: Settings | None = None, *, ai_provider=None, github_reader=None):
    config = settings or Settings()
    if config.auth_mode == "service":
        raise ValueError("Service-only configuration cannot start an HTTP API")
    engine = make_engine(config.database_url)
    factory = session_factory(engine)

    @asynccontextmanager
    async def lifespan(_):
        background = (
            start_reader(factory, config)
            if config.github_token.get_secret_value() and github_reader is None
            else None
        )
        try:
            yield
        finally:
            if background:
                stop, thread = background
                stop.set()
                thread.join(timeout=75)
            engine.dispose()

    app = FastAPI(title="KotoLancer", version="0.1.0", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.settings = config
    app.state.engine = engine

    def session(request: Request):
        with factory() as db:
            order_id = request.path_params.get("order_id")
            if order_id and not request.url.path.endswith("/visibility"):
                row = db.get(Order, order_id)
                if row and row.deleted_at:
                    raise HTTPException(404, "Заказ в корзине. Сначала восстанови его.")
            yield db

    def authorize(request: Request):
        if request.headers.get("x-koto-client") != "workspace":
            raise HTTPException(403, "Открой KotoLancer через рабочий интерфейс.")
        if config.auth_mode == "local":
            host = request.url.hostname
            peer = request.client.host if request.client else ""
            if host not in config.allowed_hosts or peer not in {"127.0.0.1", "::1", "testclient"}:
                raise HTTPException(403, "Локальный режим доступен только на этом компьютере.")
            origin = request.headers.get("origin")
            if origin and urlsplit(origin).hostname != host:
                raise HTTPException(403, "Недопустимый источник запроса.")
        elif config.auth_mode == "telegram":
            if not request.headers.get("x-telegram-init-data"):
                with factory() as db:
                    if session_valid(request, db, config):
                        if request.method not in {"GET", "HEAD"}:
                            origin_guard(request, config)
                        return
            try:
                verify_init_data(
                    request.headers.get("x-telegram-init-data", ""),
                    config.telegram_bot_token.get_secret_value(),
                    config.telegram_owner_id,
                    config.telegram_auth_max_age,
                )
            except ValueError as exc:
                raise HTTPException(401, str(exc)) from exc
        elif not secrets.compare_digest(
            request.headers.get("authorization", ""), f"Bearer {config.access_token}"
        ):
            raise HTTPException(401, "Нужен ключ доступа.")

    api = APIRouter(prefix="/api", dependencies=[Depends(authorize)])

    @app.exception_handler(StaleDataError)
    @app.exception_handler(ConflictError)
    async def conflict_handler(_, exc):
        message = str(exc) if isinstance(exc, ConflictError) else "Данные уже изменились. Обнови страницу."
        return JSONResponse(status_code=409, content={"detail": message})

    @api.get("/workspace")
    def workspace(request: Request, db: Session = Depends(session)):
        prefix = config.telegram_bot_token.get_secret_value().partition(":")[0]
        cursor = db.get(TelegramCursor, int(prefix)) if prefix.isdecimal() else None
        polled = cursor.last_poll_at if cursor else None
        if polled and polled.tzinfo is None:
            polled = polled.replace(tzinfo=UTC)
        return {
            "stage": "local-consultant",
            "auth_mode": config.auth_mode,
            "browser_session": config.auth_mode == "telegram"
            and not request.headers.get("x-telegram-init-data")
            and session_valid(request, db, config),
            "ai_connected": False,
            "ai_configured": config.ai_configured or ai_provider is not None,
            "telegram_configured": bool(
                config.telegram_owner_id and config.telegram_bot_token.get_secret_value()
            ),
            "telegram_connected": bool(polled and (now() - polled).total_seconds() < 90),
            "github_connected": False,
            "max_upload_bytes": config.max_upload_bytes,
            "database": engine.dialect.name,
        }

    @api.get("/health")
    def health(db: Session = Depends(session)):
        db.execute(text("SELECT 1"))
        return {"ok": True}

    @api.get("/profile")
    def profile(db: Session = Depends(session)):
        return current_profile(db)

    @api.put("/profile")
    def update_profile(data: ProfileUpdate, db: Session = Depends(session)):
        return revise_profile(db, data)

    @api.get("/profile/revisions")
    def revisions(db: Session = Depends(session)):
        return revision_history(db)

    @api.get("/profile/revisions/{revision_id}")
    def revision(revision_id: int, db: Session = Depends(session)):
        row = db.get(ProfileRevision, revision_id)
        if not row:
            raise HTTPException(404, "Редакция не найдена.")
        return revision_dict(row)

    def get_order(db, order_id):
        order = db.get(Order, order_id)
        if not order or order.deleted_at:
            raise HTTPException(404, "Заказ не найден.")
        return order

    def log_event(db, order_id, kind, detail):
        db.add(OrderEvent(order_id=order_id, kind=kind, detail=detail))

    def touch_order(db, order_id):
        if order_id:
            order = get_order(db, order_id)
            order.materials_revision += 1
            order.updated_at = now()

    @api.get("/orders")
    def orders(
        q: str = Query(default="", max_length=200),
        status: str = Query(default="", max_length=30),
        source: str = Query(default="", max_length=100),
        recommendation: Literal["", "discuss", "clarify", "decline", "unassessed", "outdated"] = "",
        view: Literal["active", "archived", "deleted"] = "active",
        db: Session = Depends(session),
    ):
        counts = select(Material.order_id, func.count().label("n")).group_by(Material.order_id).subquery()
        query = select(Order, func.coalesce(counts.c.n, 0)).outerjoin(counts, counts.c.order_id == Order.id)
        if view == "deleted":
            query = query.where(Order.deleted_at.is_not(None))
        else:
            query = query.where(Order.deleted_at.is_(None))
            query = query.where(
                Order.archived_at.is_(None) if view == "active" else Order.archived_at.is_not(None)
            )
        if q.strip():
            term = f"%{q.strip()}%"
            query = query.where(or_(Order.title.ilike(term), Order.description.ilike(term)))
        if status:
            query = query.where(Order.status == status)
        if source:
            query = query.where(Order.source == source)
        rows = db.execute(query.order_by(Order.updated_at.desc())).all()
        summaries = recommendation_summaries(db, {row.id: row for row, _ in rows})
        return [
            order_dict(row, count) | {"recommendation": summaries.get(row.id)}
            for row, count in rows
            if not recommendation or recommendation_bucket(summaries.get(row.id)) == recommendation
        ]

    @api.post("/orders", status_code=201)
    def create_order(data: OrderFields, db: Session = Depends(session)):
        row = Order(**data.model_dump())
        db.add(row)
        db.flush()
        log_event(db, row.id, "created", {"title": row.title})
        db.commit()
        return order_dict(row)

    @api.get("/orders/{order_id}")
    def order(order_id: str, db: Session = Depends(session)):
        row = get_order(db, order_id)
        count = db.scalar(select(func.count()).select_from(Material).where(Material.order_id == order_id))
        return order_dict(row, count)

    @api.put("/orders/{order_id}")
    def update_order(order_id: str, data: OrderUpdate, db: Session = Depends(session)):
        row = get_order(db, order_id)
        if row.version != data.expected_version:
            raise ConflictError("Заказ уже изменён. Обнови страницу перед сохранением.")
        values = data.model_dump(exclude={"expected_version"})
        changes = {
            key: {"before": getattr(row, key), "after": value}
            for key, value in values.items()
            if getattr(row, key) != value
        }
        if changes:
            for key, value in values.items():
                setattr(row, key, value)
            if "description" in changes:
                row.materials_revision += 1
            row.updated_at = now()
            log_event(db, row.id, "edited", changes)
            db.commit()
        return order_dict(
            row, db.scalar(select(func.count()).select_from(Material).where(Material.order_id == order_id))
        )

    @api.post("/orders/{order_id}/status")
    def change_status(order_id: str, data: StatusUpdate, db: Session = Depends(session)):
        row = get_order(db, order_id)
        if row.version != data.expected_version:
            raise ConflictError("Заказ уже изменён. Обнови страницу.")
        if row.accepted_at is not None:
            raise HTTPException(409, "Принятый заказ нельзя вернуть в обсуждение.")
        if row.status != data.status:
            log_event(db, row.id, "status_changed", {"before": row.status, "after": data.status})
            row.status = data.status
            row.updated_at = now()
            db.commit()
        return order_dict(row)

    @api.get("/orders/{order_id}/events")
    def events(order_id: str, db: Session = Depends(session)):
        get_order(db, order_id)
        return [
            {"id": e.id, "kind": e.kind, "detail": e.detail, "created_at": stamp(e.created_at)}
            for e in db.scalars(
                select(OrderEvent).where(OrderEvent.order_id == order_id).order_by(OrderEvent.id.desc())
            )
        ]

    @api.post("/orders/{order_id}/visibility")
    def visibility(order_id: str, data: VisibilityUpdate, db: Session = Depends(session)):
        row = db.scalar(select(Order).where(Order.id == order_id).with_for_update())
        if not row:
            raise HTTPException(404, "Заказ не найден.")
        if row.version != data.expected_version:
            raise ConflictError("Карточка изменилась. Обнови её перед действием.")
        if db.scalar(
            select(AIJob.id).where(AIJob.order_id == row.id, AIJob.status.in_(["queued", "running"]))
        ):
            raise HTTPException(409, "Дождись завершения или отмени текущий запрос Котёнка.")
        if data.action != "restore" and row.deleted_at:
            raise HTTPException(409, "Заказ уже в корзине.")
        if data.action == "archive":
            row.archived_at = now()
        elif data.action == "delete":
            row.deleted_at = now()
        else:
            row.archived_at = row.deleted_at = None
        row.updated_at = now()
        log_event(db, row.id, "visibility_changed", {"action": data.action})
        db.commit()
        return order_dict(row)

    def material_dict(db, row):
        extraction = db.get(MaterialExtract, row.id)
        return {
            "id": row.id,
            "order_id": row.order_id,
            "title": row.title,
            "kind": row.kind,
            "role": row.role,
            "text": row.text,
            "size": row.size,
            "mime": row.mime,
            "sha256": row.sha256,
            "extraction_status": row.extraction_status,
            "blocks": extraction.blocks if extraction else [],
            "warnings": extraction.warnings if extraction else [],
            "has_file": bool(row.file_key),
            "created_at": stamp(row.created_at),
        }

    @api.get("/materials")
    def materials(order_id: str | None = None, db: Session = Depends(session)):
        query = select(Material).order_by(Material.created_at.desc())
        if order_id:
            get_order(db, order_id)
            query = query.where(Material.order_id == order_id)
        else:
            query = query.where(Material.order_id.is_(None))
        return [material_dict(db, row) for row in db.scalars(query)]

    @api.post("/materials/text", status_code=201)
    def add_text(data: TextMaterial, db: Session = Depends(session)):
        touch_order(db, data.order_id)
        row = Material(**data.model_dump(), kind="text", extraction_status="ready")
        db.add(row)
        db.flush()
        if row.order_id:
            log_event(db, row.order_id, "material_added", {"title": row.title, "material_id": row.id})
        db.commit()
        return material_dict(db, row)

    @api.post("/materials/file", status_code=201)
    def add_file(file: UploadFile, order_id: str | None = None, db: Session = Depends(session)):
        if order_id:
            get_order(db, order_id)
        data = file.file.read(config.max_upload_bytes + 1)
        if len(data) > config.max_upload_bytes:
            limit = config.max_upload_bytes // (1024 * 1024)
            raise HTTPException(413, f"Превышен лимит файла ({limit} МБ). Раздели его на части.")
        try:
            fields, extraction = save_upload(data, file.filename or "file", config)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        try:
            touch_order(db, order_id)
            row = Material(order_id=order_id, **fields)
            db.add(row)
            db.flush()
            db.add(
                MaterialExtract(
                    material_id=row.id, blocks=extraction["blocks"], warnings=extraction["warnings"]
                )
            )
            if order_id:
                log_event(db, order_id, "material_added", {"title": row.title, "material_id": row.id})
            db.commit()
        except Exception:
            (config.storage_dir / fields["file_key"]).unlink(missing_ok=True)
            raise
        return material_dict(db, row)

    @api.post("/materials/{material_id}/attach")
    def attach(material_id: str, data: AttachMaterial, db: Session = Depends(session)):
        # The row lock prevents two browser tabs from attaching one inbox item twice.
        row = db.scalar(select(Material).where(Material.id == material_id).with_for_update())
        if not row:
            raise HTTPException(404, "Материал не найден.")
        if row.order_id:
            raise HTTPException(409, "Материал уже прикреплён к заказу.")
        touch_order(db, data.order_id)
        row.order_id = data.order_id
        log_event(db, row.order_id, "material_attached", {"title": row.title, "material_id": row.id})
        db.commit()
        return material_dict(db, row)

    @api.delete("/materials/{material_id}")
    def delete_inbox_material(material_id: str, db: Session = Depends(session)):
        row = db.scalar(select(Material).where(Material.id == material_id).with_for_update())
        if not row:
            raise HTTPException(404, "Материал не найден.")
        if row.order_id is not None:
            raise HTTPException(409, "Материал уже прикреплён к заказу. Удалить можно только из Входящих.")
        path = None
        if row.file_key:
            path = (config.storage_dir / row.file_key).resolve()
            if path.parent != config.storage_dir.resolve():
                raise HTTPException(409, "Некорректное расположение файла.")
        extraction = db.get(MaterialExtract, row.id)
        if extraction:
            db.delete(extraction)
            db.flush()
        db.delete(row)
        db.commit()
        if path:
            path.unlink(missing_ok=True)
        return {"deleted": True}

    @api.get("/materials/{material_id}/file")
    def download(material_id: str, db: Session = Depends(session)):
        row = db.get(Material, material_id)
        if not row or not row.file_key:
            raise HTTPException(404, "Файл не найден.")
        if row.order_id and db.get(Order, row.order_id).deleted_at:
            raise HTTPException(404, "Заказ в корзине. Сначала восстанови его.")
        path = config.storage_dir / row.file_key
        if not path.is_file():
            raise HTTPException(404, "Оригинал отсутствует в хранилище.")
        return FileResponse(
            path,
            filename=safe_filename(row.title),
            media_type=row.mime,
            headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"},
        )

    api.include_router(work_routes(session, order_dict))
    api.include_router(profile_routes(session))
    api.include_router(consultant_routes(session, config, ai_provider))
    api.include_router(integration_routes(session, config, github_reader))
    api.include_router(github_review_routes(session, config, ai_provider))
    app.include_router(api)
    app.include_router(browser_routes(session, config))

    @app.middleware("http")
    async def response_headers(request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        if request.url.path.startswith(("/api/", "/auth/")):
            response.headers["Cache-Control"] = "no-store"
        return response

    dist = ROOT / "frontend" / "dist"
    if dist.is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/")
        def frontend():
            return FileResponse(dist / "index.html")

    return app


app = create_app()
