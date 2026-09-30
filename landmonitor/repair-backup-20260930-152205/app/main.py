"""FastAPI: API для веб-панели инспектора + запуск Telegram-бота в том же процессе."""
import asyncio
import base64
import hmac
import json
import logging
import os
import random
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import bot as bot_mod
from . import db, seed, services

load_dotenv(db.BASE_DIR / ".env")
logging.basicConfig(level=logging.WARNING)

STATIC_DIR = Path(__file__).resolve().parent / "static"
ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp"}
MAX_UPLOAD = 10 * 1024 * 1024


@asynccontextmanager
async def lifespan(app: FastAPI):
    seed.seed_if_empty()
    task = None
    token = os.getenv("BOT_TOKEN", "").strip()
    if token:
        task = asyncio.create_task(bot_mod.run_bot(token))
    else:
        logging.warning("BOT_TOKEN не задан — Telegram-бот выключен, работает только веб-панель.")
    yield
    if task:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


app = FastAPI(title="Цифровой мониторинг земель", lifespan=lifespan)
PASSWORD = os.getenv("INSPECTOR_PASSWORD", "").strip()


@app.middleware("http")
async def basic_auth(request: Request, call_next):
    """Необязательная защита панели: задайте INSPECTOR_PASSWORD в .env (логин: inspector)."""
    if PASSWORD and request.url.path != "/healthz":
        ok = False
        header = request.headers.get("authorization", "")
        if header.lower().startswith("basic "):
            try:
                user, _, pwd = base64.b64decode(header[6:]).decode().partition(":")
                ok = user == "inspector" and hmac.compare_digest(pwd, PASSWORD)
            except Exception:
                ok = False
        if not ok:
            return Response("Нужен вход", status_code=401, headers={"WWW-Authenticate": 'Basic realm="Inspector"'})
    return await call_next(request)


app.mount("/uploads", StaticFiles(directory=db.UPLOAD_DIR), name="uploads")


class StatusIn(BaseModel):
    status: Literal["ok", "signal", "violation", "in_progress", "resolved"]
    deadline: Optional[str] = None
    violation_type: Optional[str] = None
    note: Optional[str] = None


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/healthz")
def healthz():
    """Для проверок хостинга и «будильника» (без пароля)."""
    return {"ok": True}


@app.get("/api/meta")
def meta():
    return {
        "statuses": services.STATUS_LABELS,
        "violation_types": services.VIOLATION_TYPES,
        "bot_enabled": bot_mod.bot is not None,
    }


@app.get("/api/parcels")
def parcels():
    return services.all_features()


@app.get("/api/parcels/{pid}")
def parcel(pid: int):
    p = services.get_parcel(pid)
    if not p:
        raise HTTPException(404, "Участок не найден")
    return p


@app.post("/api/parcels/{pid}/status")
async def set_status(pid: int, body: StatusIn):
    p, notifications = services.update_status(pid, body.status, body.deadline, body.violation_type, body.note)
    if not p:
        raise HTTPException(404, "Участок не найден")
    for chat_id, text in notifications:
        asyncio.create_task(bot_mod.notify(chat_id, text))
    return p


@app.post("/api/parcels/{pid}/photos")
async def upload_photo(pid: int, file: UploadFile = File(...), caption: str = Form("")):
    if not services.get_parcel(pid):
        raise HTTPException(404, "Участок не найден")
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(400, "Допустимы форматы JPG, PNG, WEBP")
    data = await file.read()
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "Файл больше 10 МБ")
    name = f"{uuid.uuid4().hex}{ext}"
    (db.UPLOAD_DIR / name).write_bytes(data)
    services.add_photo(pid, name, caption.strip() or "Фото инспектора", "inspector")
    services.log_event(pid, "Инспектор добавил фото")
    return services.get_parcel(pid)


# ---------- демо-помощники (на случай проблем с Telegram во время защиты) ----------

DEMO_TEXTS = [
    "Участок зарос сорняками, не обрабатывается несколько лет",
    "Стихийная свалка строительного мусора",
    "Похоже на самозахват: появился забор за границей участка",
]


@app.post("/api/demo/signal")
def demo_signal():
    candidates = [f for f in services.all_features()["features"]
                  if f["properties"]["kind"] == "parcel" and f["properties"]["status"] in ("ok", "resolved")]
    if not candidates:
        raise HTTPException(409, "Нет свободных участков для демо-сигнала")
    lat, lon = services.centroid(random.choice(candidates)["geometry"])
    return services.create_signal(lat, lon, random.choice(DEMO_TEXTS))


@app.post("/api/demo/reset")
def demo_reset():
    seed.reset()
    return {"ok": True}


# ---------- аналитика, акт, спутниковый автосигнал, живой канал ----------

@app.get("/api/stats")
def stats():
    return services.stats()


@app.get("/api/parcels/{pid}/act", response_class=HTMLResponse)
def act(pid: int):
    page = services.act_html(pid)
    if page is None:
        raise HTTPException(404, "Участок не найден")
    return page


@app.post("/api/signals/{sid}/split")
def split_signal(sid: int):
    p = services.split_signal(sid)
    if not p:
        raise HTTPException(400, "Отделять нечего: обращение не найдено или оно единственное в этой точке")
    return p


@app.post("/api/parcels/{pid}/satellite-signal")
def satellite_signal(pid: int):
    p = services.satellite_signal(pid)
    if not p:
        raise HTTPException(404, "Участок не найден")
    return p


@app.get("/api/stream")
async def stream(request: Request):
    """Server-Sent Events: панель обновляется сразу после любой записи в БД (без опроса раз в N секунд)."""
    async def gen():
        last = -1
        for tick in range(110):  # ~55 секунд, затем браузер сам переподключается
            if await request.is_disconnected():
                return
            if db.changes != last:
                last = db.changes
                yield f"data: {json.dumps({'v': last})}\n\n"
            elif tick % 20 == 0:
                yield ": keepalive\n\n"
            await asyncio.sleep(0.5)
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
