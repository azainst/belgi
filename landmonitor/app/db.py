"""Тонкая обёртка над SQLite: один файл БД, без внешней инфраструктуры."""
import sqlite3
import threading
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
DB_PATH = DATA_DIR / "landmonitor.db"

DATA_DIR.mkdir(exist_ok=True)
UPLOAD_DIR.mkdir(exist_ok=True)

lock = threading.RLock()
changes = 0  # счётчик записей: по нему живой канал (SSE) понимает, что пора обновить панель
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
conn.row_factory = sqlite3.Row

SCHEMA = """
CREATE TABLE IF NOT EXISTS parcels (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    cadastral      TEXT,
    purpose        TEXT,
    area_ha        REAL,
    geom           TEXT NOT NULL,              -- GeoJSON geometry (Polygon / MultiPolygon / Point)
    status         TEXT NOT NULL DEFAULT 'ok', -- ok | signal | violation | in_progress | resolved
    kind           TEXT NOT NULL DEFAULT 'parcel', -- parcel | signal (точка от жителя вне известных участков)
    violation_type TEXT,
    deadline       TEXT,                       -- YYYY-MM-DD
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    risk           REAL,                       -- демо-индекс «спутникового анализа», 0..1
    flagged_at     TEXT,                       -- когда выявлено нарушение
    resolved_at    TEXT                        -- когда устранено
);
CREATE TABLE IF NOT EXISTS photos (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    parcel_id  INTEGER NOT NULL,
    filename   TEXT NOT NULL,
    caption    TEXT,
    source     TEXT NOT NULL,                  -- citizen | inspector
    signal_id  INTEGER,                        -- какое обращение прислало это фото (для «отделить»)
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    parcel_id  INTEGER NOT NULL,
    text       TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS signals (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    parcel_id  INTEGER NOT NULL,
    chat_id    INTEGER,
    username   TEXT,
    text       TEXT,
    lat        REAL,
    lon        REAL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS applications (
    track       TEXT PRIMARY KEY,
    subject     TEXT NOT NULL,
    status      TEXT NOT NULL,
    explanation TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    subject_kk  TEXT,
    explanation_kk TEXT
);
CREATE TABLE IF NOT EXISTS users (
    chat_id INTEGER PRIMARY KEY,
    lang    TEXT NOT NULL DEFAULT 'ru'
);
"""

MIGRATIONS = [  # для баз, созданных прошлой версией
    ("parcels", "risk", "REAL"), ("parcels", "flagged_at", "TEXT"), ("parcels", "resolved_at", "TEXT"),
    ("applications", "subject_kk", "TEXT"), ("applications", "explanation_kk", "TEXT"),
    ("photos", "signal_id", "INTEGER"),
]


def init() -> None:
    with lock:
        conn.executescript(SCHEMA)
        for table, col, typ in MIGRATIONS:
            cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
            if col not in cols:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
        conn.commit()


def drop_all() -> None:
    with lock:
        for t in ("parcels", "photos", "events", "signals", "applications", "users"):
            conn.execute(f"DROP TABLE IF EXISTS {t}")
        conn.commit()
    init()


def query(sql: str, args: tuple = ()) -> list[dict]:
    with lock:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]


def one(sql: str, args: tuple = ()) -> dict | None:
    rows = query(sql, args)
    return rows[0] if rows else None


def execute(sql: str, args: tuple = ()) -> int:
    global changes
    with lock:
        cur = conn.execute(sql, args)
        conn.commit()
        changes += 1
        return cur.lastrowid
