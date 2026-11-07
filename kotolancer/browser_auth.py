"""Two-channel owner approval: Telegram capability alone cannot claim a browser session."""

import hashlib
import secrets
from datetime import UTC, timedelta
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import delete, func, select, update

from .models import BrowserLogin, BrowserSession, ProfileHead, now

SESSION = "__Host-koto-session"
PENDING = "__Host-koto-login"
LOGIN_SECONDS = 300
SESSION_SECONDS = 30 * 86400


def hashed(value):
    return hashlib.sha256(value.encode()).hexdigest()


def binding(settings):
    return hashed(str(settings.telegram_owner_id) + ":" + settings.telegram_bot_token.get_secret_value())


def future(value):
    return value.replace(tzinfo=UTC) > now() if value.tzinfo is None else value > now()


def origin_guard(request, settings):
    parsed = urlsplit(settings.mini_app_url)
    expected = f"{parsed.scheme}://{parsed.netloc}" if parsed.netloc else ""
    if (
        request.headers.get("x-koto-client") != "workspace"
        or not expected
        or request.headers.get("origin") != expected
    ):
        raise HTTPException(403, "Открой вход на сайте своей мастерской.")


def session_valid(request, db, settings):
    token = request.cookies.get(SESSION, "")
    if not token or len(token) > 100:
        return False
    row = db.get(BrowserSession, hashed(token))
    return bool(row and row.binding == binding(settings) and future(row.expires_at))


def cookie(response, name, value, seconds):
    response.set_cookie(name, value, max_age=seconds, httponly=True, secure=True, samesite="strict", path="/")


def login_by_approval(db, token, settings):
    if len(token) != 43:
        return None
    row = db.scalar(select(BrowserLogin).where(BrowserLogin.approval_hash == hashed(token)).with_for_update())
    return row if row and row.binding == binding(settings) and future(row.expires_at) else None


def browser_routes(session, settings):
    router = APIRouter(prefix="/auth/browser")

    @router.get("/info")
    def info():
        return JSONResponse(
            {"enabled": settings.auth_mode == "telegram", "session_days": 30},
            headers={"Cache-Control": "no-store"},
        )

    def guard(request):
        if settings.auth_mode != "telegram":
            raise HTTPException(404, "Браузерный вход здесь выключен.")
        origin_guard(request, settings)

    @router.post("/start")
    def start(request: Request, db=Depends(session)):
        guard(request)
        instant = now()
        # Serialize public creation limits with an existing singleton, including across API processes.
        db.scalar(select(ProfileHead).where(ProfileHead.id == 1).with_for_update())
        db.execute(delete(BrowserLogin).where(BrowserLogin.created_at < instant - timedelta(days=1)))
        db.execute(delete(BrowserSession).where(BrowserSession.expires_at < instant))
        recent = db.scalar(
            select(func.count())
            .select_from(BrowserLogin)
            .where(BrowserLogin.created_at > instant - timedelta(seconds=LOGIN_SECONDS))
        )
        if recent >= 20:
            raise HTTPException(429, "Слишком много запросов входа. Подожди пять минут.")
        previous = request.cookies.get(PENDING, "")
        if previous:
            row = db.scalar(select(BrowserLogin).where(BrowserLogin.browser_hash == hashed(previous)))
            if row:
                if (instant - row.created_at.replace(tzinfo=UTC)).total_seconds() < 5:
                    raise HTTPException(429, "Подожди несколько секунд перед новым запросом.")
                if row.status in {"pending", "approved"}:
                    row.status = "cancelled"
        browser, approval = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        row = BrowserLogin(
            browser_hash=hashed(browser),
            approval_hash=hashed(approval),
            binding=binding(settings),
            code=f"{secrets.randbelow(1000000):06d}",
            expires_at=instant + timedelta(seconds=LOGIN_SECONDS),
        )
        db.add(row)
        db.commit()
        response = JSONResponse(
            {
                "status": "pending",
                "code": row.code,
                "expires_in": LOGIN_SECONDS,
                "telegram_url": f"https://t.me/{settings.telegram_bot_username}?start=login_{approval}",
            }
        )
        cookie(response, PENDING, browser, LOGIN_SECONDS)
        response.headers["Cache-Control"] = "no-store"
        return response

    @router.post("/complete")
    def complete(request: Request, db=Depends(session)):
        guard(request)
        if session_valid(request, db, settings):
            return JSONResponse({"status": "authenticated"}, headers={"Cache-Control": "no-store"})
        token = request.cookies.get(PENDING, "")
        row = (
            db.scalar(
                select(BrowserLogin).where(BrowserLogin.browser_hash == hashed(token)).with_for_update()
            )
            if token
            else None
        )
        if not row or row.binding != binding(settings) or not future(row.expires_at):
            return JSONResponse({"status": "expired"}, headers={"Cache-Control": "no-store"})
        if row.status != "approved":
            return JSONResponse({"status": row.status}, headers={"Cache-Control": "no-store"})
        # Conditional consume prevents two concurrent polls from minting two sessions.
        changed = db.execute(
            update(BrowserLogin)
            .where(BrowserLogin.id == row.id, BrowserLogin.status == "approved")
            .values(status="consumed")
        )
        if changed.rowcount != 1:
            raise HTTPException(409, "Этот вход уже использован. Начни новый.")
        secret = secrets.token_urlsafe(32)
        db.add(
            BrowserSession(
                token_hash=hashed(secret),
                binding=binding(settings),
                expires_at=now() + timedelta(seconds=SESSION_SECONDS),
            )
        )
        db.commit()
        response = JSONResponse({"status": "authenticated"}, headers={"Cache-Control": "no-store"})
        cookie(response, SESSION, secret, SESSION_SECONDS)
        response.delete_cookie(PENDING, path="/", secure=True, httponly=True, samesite="strict")
        return response

    @router.post("/logout")
    def logout(request: Request, db=Depends(session)):
        guard(request)
        token = request.cookies.get(SESSION, "")
        db.execute(delete(BrowserSession).where(BrowserSession.token_hash == hashed(token)))
        pending = request.cookies.get(PENDING, "")
        db.execute(
            update(BrowserLogin)
            .where(
                BrowserLogin.browser_hash == hashed(pending), BrowserLogin.status.in_(["pending", "approved"])
            )
            .values(status="cancelled")
        )
        db.commit()
        response = JSONResponse({"status": "logged_out"}, headers={"Cache-Control": "no-store"})
        for name in [SESSION, PENDING]:
            response.delete_cookie(name, path="/", secure=True, httponly=True, samesite="strict")
        return response

    return router
