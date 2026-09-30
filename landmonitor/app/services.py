"""Бизнес-логика: участки, сигналы жителей, жизненный цикл нарушения."""
import json
import math
from datetime import date, datetime, timedelta

import html
import os
import re

from . import db, i18n

STATUSES = ("ok", "signal", "violation", "in_progress", "resolved")
STATUS_LABELS = {
    "ok": "Нарушений нет",
    "signal": "Сигнал / первичная проверка",
    "violation": "Выявлено нарушение",
    "in_progress": "В процессе устранения",
    "resolved": "Устранено / возвращено государству",
}
VIOLATION_TYPES = [
    "Неиспользование участка",
    "Самозахват",
    "Стихийная свалка",
    "Нецелевое использование",
]


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------- геометрия ----------

def _ring_contains(ring: list, x: float, y: float) -> bool:
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def point_in_geom(x: float, y: float, geom: dict) -> bool:
    """x = долгота, y = широта."""
    t = geom.get("type")
    if t == "Polygon":
        polys = [geom["coordinates"]]
    elif t == "MultiPolygon":
        polys = geom["coordinates"]
    else:
        return False
    for poly in polys:
        if _ring_contains(poly[0], x, y) and not any(_ring_contains(h, x, y) for h in poly[1:]):
            return True
    return False


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dy = (lat2 - lat1) * 110540
    dx = (lon2 - lon1) * 111320 * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot(dx, dy)


# Сигналы ближе этого расстояния считаем одним и тем же местом (один участок принимает несколько
# обращений; отметка статуса уходит сразу всем, кто их отправил). Радиус можно задать через .env
# (SIGNAL_MERGE_RADIUS_M), разумный диапазон — 100-200 м. Если в радиус попали два РАЗНЫХ нарушения,
# инспектор может отделить одно обращение в новую точку функцией split_signal ниже.
MERGE_RADIUS_M = int(os.getenv("SIGNAL_MERGE_RADIUS_M", "150"))


def centroid(geom: dict) -> tuple[float, float]:
    """Возвращает (lat, lon) — среднее по внешнему контуру."""
    t = geom["type"]
    if t == "Point":
        return geom["coordinates"][1], geom["coordinates"][0]
    ring = geom["coordinates"][0] if t == "Polygon" else geom["coordinates"][0][0]
    pts = ring[:-1] if len(ring) > 1 and ring[0] == ring[-1] else ring
    return (sum(p[1] for p in pts) / len(pts), sum(p[0] for p in pts) / len(pts))


def area_ha(geom: dict) -> float | None:
    """Приблизительная площадь полигона в гектарах (локальная проекция)."""
    if geom["type"] == "Polygon":
        polys = [geom["coordinates"]]
    elif geom["type"] == "MultiPolygon":
        polys = geom["coordinates"]
    else:
        return None
    total = 0.0
    for poly in polys:
        lat0 = math.radians(poly[0][0][1])
        kx, ky = 111320 * math.cos(lat0), 110540

        def shoelace(ring):
            s = 0.0
            for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
                s += (x1 * kx) * (y2 * ky) - (x2 * kx) * (y1 * ky)
            return abs(s) / 2

        total += shoelace([tuple(p[:2]) for p in poly[0]]) - sum(
            shoelace([tuple(p[:2]) for p in h]) for h in poly[1:]
        )
    return round(total / 10000, 2)


# ---------- чтение ----------

def all_features() -> dict:
    rows = db.query(
        """SELECT p.*, (SELECT COUNT(*) FROM photos WHERE parcel_id = p.id) AS photos_count
           FROM parcels p"""
    )
    feats = []
    for r in rows:
        geom = json.loads(r.pop("geom"))
        feats.append({"type": "Feature", "id": r["id"], "geometry": geom, "properties": r})
    return {"type": "FeatureCollection", "features": feats}


def get_parcel(pid: int) -> dict | None:
    p = db.one("SELECT * FROM parcels WHERE id = ?", (pid,))
    if not p:
        return None
    p["geom"] = json.loads(p["geom"])
    p["photos"] = db.query("SELECT * FROM photos WHERE parcel_id = ? ORDER BY id DESC", (pid,))
    for ph in p["photos"]:
        ph["signal_code"] = signal_code(ph["signal_id"]) if ph.get("signal_id") else None
    p["events"] = db.query("SELECT * FROM events WHERE parcel_id = ? ORDER BY id DESC", (pid,))
    p["signals_count"] = db.one("SELECT COUNT(*) AS c FROM signals WHERE parcel_id = ?", (pid,))["c"]
    p["signal_codes"] = [signal_code(r["id"]) for r in db.query(
        "SELECT id FROM signals WHERE parcel_id = ? ORDER BY id", (pid,))]
    return p


# ---------- запись ----------

def log_event(pid: int, text: str) -> None:
    db.execute("INSERT INTO events(parcel_id, text, created_at) VALUES (?,?,?)", (pid, text, now()))


def touch(pid: int) -> None:
    db.execute("UPDATE parcels SET updated_at = ? WHERE id = ?", (now(), pid))


def add_photo(pid: int, filename: str, caption: str, source: str, signal_id: int | None = None) -> None:
    db.execute(
        "INSERT INTO photos(parcel_id, filename, caption, source, signal_id, created_at) VALUES (?,?,?,?,?,?)",
        (pid, filename, caption, source, signal_id, now()),
    )
    touch(pid)


def signal_code(sid: int) -> str:
    """Короткий номер обращения, который получает житель и может использовать для поиска статуса."""
    return f"NK-{sid:05d}"


def parse_signal_code(text: str) -> int | None:
    m = re.search(r"NK-0*(\d+)", text, re.IGNORECASE)
    return int(m.group(1)) if m else None


def get_signal_status(sid: int) -> dict | None:
    row = db.one(
        """SELECT s.id, s.created_at, p.status, p.deadline FROM signals s
           JOIN parcels p ON p.id = s.parcel_id WHERE s.id = ?""",
        (sid,),
    )
    if not row:
        return None
    row["code"] = signal_code(row["id"])
    return row


def create_signal(lat: float, lon: float, text: str = "", photos: list[str] | None = None,
                  chat_id: int | None = None, username: str | None = None) -> dict:
    """Сигнал жителя, с одним или несколькими фото (например, альбом из Telegram). Если точка
    внутри известного участка — участок жёлтый, иначе создаётся новая точка «на проверку».
    Всё под одной блокировкой: два сигнала из одной точки, пришедшие практически одновременно,
    не должны создать две разные точки на карте."""
    with db.lock:
        return _create_signal_locked(lat, lon, text, photos, chat_id, username)


def _create_signal_locked(lat, lon, text, photos, chat_id, username) -> dict:
    text = (text or "").strip()
    matched = None
    for r in db.query("SELECT id, geom, status FROM parcels WHERE kind = 'parcel'"):
        if point_in_geom(lon, lat, json.loads(r["geom"])):
            matched = r
            break

    ts = now()
    merged = False
    if not matched:  # склеиваем с уже открытой точкой поблизости
        for r in db.query("SELECT id, geom FROM parcels WHERE kind = 'signal' AND status IN ('signal','violation','in_progress')"):
            g = json.loads(r["geom"])
            if distance_m(lat, lon, g["coordinates"][1], g["coordinates"][0]) <= MERGE_RADIUS_M:
                matched, merged = {"id": r["id"], "status": "signal"}, True
                break
    if matched:
        pid = matched["id"]
        if matched["status"] in ("ok", "resolved"):
            db.execute("UPDATE parcels SET status = 'signal' WHERE id = ?", (pid,))
    else:
        geom = json.dumps({"type": "Point", "coordinates": [lon, lat]})
        pid = db.execute(
            """INSERT INTO parcels(cadastral, purpose, area_ha, geom, status, kind, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            ("Не определён", "Уточняется при проверке", None, geom, "signal", "signal", ts, ts),
        )

    sid = db.execute(
        "INSERT INTO signals(parcel_id, chat_id, username, text, lat, lon, created_at) VALUES (?,?,?,?,?,?,?)",
        (pid, chat_id, username, text, lat, lon, ts),
    )
    for i, fn in enumerate(photos or []):
        caption = text or ("Фото от жителя" if len(photos) == 1 else f"Фото от жителя ({i + 1}/{len(photos)})")
        add_photo(pid, fn, caption, "citizen", signal_id=sid)
    log_event(pid, f"Сигнал №{sid} от жителя" + (f": {text}" if text else ""))
    touch(pid)
    return {"signal_id": sid, "parcel_id": pid, "code": signal_code(sid),
            "matched": bool(matched) and not merged, "merged": merged}


def update_status(pid: int, status: str, deadline: str | None = None,
                  violation_type: str | None = None, note: str | None = None):
    """Меняет статус. Возвращает (parcel, [(chat_id, text)]) — уведомления жителям."""
    if status not in STATUSES:
        raise ValueError("unknown status")
    p = db.one("SELECT * FROM parcels WHERE id = ?", (pid,))
    if not p:
        return None, []

    if status in ("violation", "in_progress"):
        deadline = deadline or p["deadline"] or (date.today() + timedelta(days=30)).isoformat()
        violation_type = violation_type or p["violation_type"] or VIOLATION_TYPES[0]
    elif status == "ok":
        deadline, violation_type = None, None
    else:  # signal / resolved — сохраняем историю
        deadline = deadline or p["deadline"]
        violation_type = violation_type or p["violation_type"]

    flagged_at = p["flagged_at"] or (now() if status == "violation" else None)
    resolved_at = now() if status == "resolved" else None
    db.execute(
        "UPDATE parcels SET status=?, deadline=?, violation_type=?, updated_at=?, flagged_at=?, resolved_at=? WHERE id=?",
        (status, deadline, violation_type, now(), flagged_at, resolved_at, pid),
    )
    line = f"Статус: {STATUS_LABELS[p['status']]} → {STATUS_LABELS[status]}"
    if deadline and status in ("violation", "in_progress"):
        line += f". Срок устранения: {deadline}"
    if note:
        line += f". Комментарий: {note}"
    log_event(pid, line)

    notifications = []
    if p["status"] != status:
        for s in db.query(
            """SELECT MIN(s.id) AS sid, s.chat_id, COALESCE(u.lang, 'ru') AS lang FROM signals s
               LEFT JOIN users u ON u.chat_id = s.chat_id
               WHERE s.parcel_id=? AND s.chat_id IS NOT NULL GROUP BY s.chat_id""",
            (pid,),
        ):
            msg = i18n.notify_text(s["lang"], status, signal_code(s["sid"]), deadline)
            if msg:
                notifications.append((s["chat_id"], msg))
    return get_parcel(pid), notifications


def split_signal(sid: int) -> dict | None:
    """Отделяет одно обращение из общей точки в отдельную новую точку.

    Нужно, когда несколько обращений склеились по радиусу (SIGNAL_MERGE_RADIUS_M), но на месте
    на самом деле два разных нарушения. Возвращает новую точку со своим фото и статусом «Сигнал»;
    остальные обращения и статус старой точки не трогаются. Если обращение и так единственное
    в этой точке, отделять нечего — возвращает None."""
    with db.lock:
        row = db.one("SELECT * FROM signals WHERE id = ?", (sid,))
        if not row:
            return None
        old_pid = row["parcel_id"]
        count = db.one("SELECT COUNT(*) AS c FROM signals WHERE parcel_id = ?", (old_pid,))["c"]
        if count <= 1:
            return None
        ts = now()
        geom = json.dumps({"type": "Point", "coordinates": [row["lon"], row["lat"]]})
        new_pid = db.execute(
            """INSERT INTO parcels(cadastral, purpose, area_ha, geom, status, kind, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            ("Не определён", "Уточняется при проверке", None, geom, "signal", "signal", ts, ts),
        )
        db.execute("UPDATE signals SET parcel_id = ? WHERE id = ?", (new_pid, sid))
        db.execute("UPDATE photos SET parcel_id = ? WHERE signal_id = ?", (new_pid, sid))
        code = signal_code(sid)
        log_event(new_pid, f"Обращение {code} отделено в отдельную точку (было объединено с другими рядом)")
        log_event(old_pid, f"Обращение {code} отделено в отдельную точку — оказалось другим нарушением")
        touch(old_pid)
        return get_parcel(new_pid)


def get_my_signal_detail(chat_id: int, sid: int) -> dict | None:
    """Полная карточка ОДНОГО обращения для его же автора: точка на карте и фото — не отдаётся
    по номеру всем подряд (публичная проверка статуса не показывает ни то, ни другое)."""
    row = db.one(
        """SELECT s.id, s.lat, s.lon, s.text, s.created_at, p.status, p.deadline, p.violation_type
           FROM signals s JOIN parcels p ON p.id = s.parcel_id
           WHERE s.id = ? AND s.chat_id = ?""",
        (sid, chat_id),
    )
    if not row:
        return None
    row["code"] = signal_code(row["id"])
    row["photos"] = db.query("SELECT filename, caption FROM photos WHERE signal_id = ? ORDER BY id", (sid,))
    return row


def get_application(track: str) -> dict | None:
    return db.one("SELECT * FROM applications WHERE track = ?", (track.strip().upper(),))


# ---------- пользователи бота ----------

def get_lang(chat_id: int) -> str | None:
    u = db.one("SELECT lang FROM users WHERE chat_id = ?", (chat_id,))
    return u["lang"] if u else None


def set_lang(chat_id: int, lang: str) -> None:
    db.execute("INSERT INTO users(chat_id, lang) VALUES (?,?) ON CONFLICT(chat_id) DO UPDATE SET lang=excluded.lang",
               (chat_id, lang))


def my_signals(chat_id: int) -> list[dict]:
    return db.query(
        """SELECT s.id, s.created_at, p.status FROM signals s JOIN parcels p ON p.id = s.parcel_id
           WHERE s.chat_id = ? ORDER BY s.id DESC LIMIT 10""", (chat_id,))


# ---------- аналитика ----------

def stats() -> dict:
    rows = db.query("SELECT * FROM parcels")
    today = date.today().isoformat()
    by_status = {k: 0 for k in STATUSES}
    for r in rows:
        by_status[r["status"]] += 1
    overdue = sum(1 for r in rows if r["deadline"] and r["status"] in ("violation", "in_progress") and r["deadline"] < today)
    by_type: dict[str, int] = {}
    for r in rows:
        if r["violation_type"] and r["status"] in ("violation", "in_progress", "resolved"):
            by_type[r["violation_type"]] = by_type.get(r["violation_type"], 0) + 1
    days = []
    for r in rows:
        if r["flagged_at"] and r["resolved_at"]:
            d = (datetime.fromisoformat(r["resolved_at"]) - datetime.fromisoformat(r["flagged_at"])).days
            days.append(max(d, 0))
    week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
    signals_week = db.one("SELECT COUNT(*) AS c FROM signals WHERE created_at >= ?", (week_ago,))["c"]
    suspects = sum(1 for r in rows if r["status"] == "ok" and (r["risk"] or 0) >= RISK_THRESHOLD)
    return {
        "total": len(rows), "by_status": by_status, "overdue": overdue, "by_type": by_type,
        "avg_days_to_resolve": round(sum(days) / len(days), 1) if days else None,
        "signals_week": signals_week, "signals_total": db.one("SELECT COUNT(*) AS c FROM signals")["c"],
        "suspects": suspects,
        "area_ha_in_violation": round(sum(r["area_ha"] or 0 for r in rows if r["status"] in ("violation", "in_progress")), 1),
    }


# ---------- «спутниковый анализ» (демо-модель) ----------

RISK_THRESHOLD = 0.7


def satellite_signal(pid: int) -> dict | None:
    """Автоматически создаёт проверку по подозрительному участку (демонстрация масштабирования)."""
    p = db.one("SELECT * FROM parcels WHERE id = ?", (pid,))
    if not p:
        return None
    if p["status"] in ("ok", "resolved"):
        db.execute("UPDATE parcels SET status='signal' WHERE id=?", (pid,))
    log_event(pid, f"Автосигнал спутникового мониторинга (демо-модель): индекс подозрения {p['risk']:.2f}, "
                   "участок похож на неиспользуемый")
    touch(pid)
    return get_parcel(pid)


# ---------- акт осмотра ----------

def act_html(pid: int) -> str | None:
    p = get_parcel(pid)
    if not p:
        return None
    e = lambda v: html.escape(str(v)) if v not in (None, "") else "—"
    lat, lon = centroid(p["geom"])
    photos = "".join(
        f'<figure><img src="/uploads/{html.escape(ph["filename"])}" alt=""><figcaption>{e(ph["caption"])}</figcaption></figure>'
        for ph in p["photos"][:6])
    events = "".join(f"<li>{e(ev['created_at'][:16])} — {e(ev['text'])}</li>" for ev in reversed(p["events"]))
    return f"""<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>Акт осмотра {e(p['cadastral'])}</title>
<style>
body{{font:14px/1.5 "Times New Roman",serif;max-width:800px;margin:24px auto;padding:0 20px;color:#000}}
h1{{font-size:20px;text-align:center;margin-bottom:2px}} .sub{{text-align:center;margin-bottom:22px}}
table{{border-collapse:collapse;width:100%;margin:12px 0}} td{{border:1px solid #444;padding:6px 9px;vertical-align:top}} td:first-child{{width:34%;font-weight:bold}}
figure{{display:inline-block;width:31%;margin:4px;vertical-align:top}} img{{width:100%;height:150px;object-fit:cover}} figcaption{{font-size:11px}}
.sign{{margin-top:40px;display:flex;justify-content:space-between}} .sign div{{width:45%;border-top:1px solid #000;padding-top:4px;font-size:12px}}
.bar{{background:#eee;padding:8px 12px;margin-bottom:14px;font:13px sans-serif}} @media print{{.bar{{display:none}}}}
</style></head><body>
<div class="bar">Черновик акта сформирован автоматически. <a href="#" onclick="window.print();return false">Печать / сохранить в PDF</a></div>
<h1>АКТ ОСМОТРА ЗЕМЕЛЬНОГО УЧАСТКА</h1><div class="sub">от {date.today().strftime('%d.%m.%Y')}</div>
<table>
<tr><td>Кадастровый номер</td><td>{e(p['cadastral'])}</td></tr>
<tr><td>Целевое назначение</td><td>{e(p['purpose'])}</td></tr>
<tr><td>Площадь</td><td>{e(str(p['area_ha']) + ' га' if p['area_ha'] else None)}</td></tr>
<tr><td>Координаты (центр)</td><td>{lat:.5f}, {lon:.5f}</td></tr>
<tr><td>Текущий статус</td><td>{e(STATUS_LABELS[p['status']])}</td></tr>
<tr><td>Выявленное нарушение</td><td>{e(p['violation_type'])}</td></tr>
<tr><td>Срок устранения</td><td>{e(p['deadline'])}</td></tr>
<tr><td>Обращений жителей</td><td>{p['signals_count']}</td></tr>
</table>
<h3>Фотофиксация</h3>{photos or '<p>Фото не приложены.</p>'}
<h3>Ход рассмотрения</h3><ul>{events}</ul>
<div class="sign"><div>Инспектор (ФИО, подпись)</div><div>Дата</div></div>
</body></html>"""
