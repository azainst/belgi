"""Загрузка тестовых данных: участки из GeoJSON и тестовые трек-номера обращений.

Запуск вручную:
    python -m app.seed --reset                       # сбросить БД и загрузить data/parcels.geojson
    python -m app.seed --reset --geojson путь.geojson  # загрузить GeoJSON организаторов
"""
import argparse
import json
import zlib
from datetime import date, timedelta
from pathlib import Path

from . import db, services

DEFAULT_GEOJSON = db.DATA_DIR / "parcels.geojson"

# Организаторы могут назвать поля по-разному — ищем по нескольким вариантам.
CADASTRAL_KEYS = ("cadastral_number", "cadastral", "cad_num", "cadastre_number", "kadastr", "number", "name", "id")
PURPOSE_KEYS = ("purpose", "target_use", "land_purpose", "naznachenie", "use", "category")
AREA_KEYS = ("area_ha", "area", "square", "площадь")

APPLICATIONS = [
    # (трек, тема, статус, пояснение, тема_kk, пояснение_kk)
    ("KZ-2026-042", "Изменение целевого назначения участка", "На рассмотрении",
     "Заявление зарегистрировано и находится на рассмотрении в отделе земельных отношений. "
     "Ориентировочный срок рассмотрения — до 15 рабочих дней.",
     "Телімнің нысаналы мақсатын өзгерту",
     "Өтініш тіркелді және жер қатынастары бөлімінде қарастырылуда. Қарау мерзімі шамамен 15 жұмыс күніне дейін."),
    ("KZ-2026-017", "Получение участка под ИЖС", "Назначен выезд инспектора",
     "Для проверки заявления назначен выезд инспектора на участок. Пожалуйста, обеспечьте доступ "
     "на территорию. Дата выезда будет сообщена дополнительно.",
     "ЖТС үшін телім алу",
     "Өтінішті тексеру үшін инспектордың телімге шығуы тағайындалды. Аумаққа қолжетімділікті қамтамасыз етіңіз. "
     "Шығу күні қосымша хабарланады."),
    ("KZ-2026-008", "Продление договора аренды", "Одобрено",
     "Заявление одобрено. Дополнительное соглашение к договору аренды готово к подписанию — "
     "обратитесь в ЦОН с документом, удостоверяющим личность.",
     "Жалдау шартын ұзарту",
     "Өтініш мақұлданды. Жалдау шартына қосымша келісім қол қоюға дайын — жеке басты куәландыратын құжатпен ХҚКО-ға жүгініңіз."),
    ("KZ-2026-055", "Изменение целевого назначения участка", "Отказ",
     "В удовлетворении заявления отказано: испрашиваемое назначение не соответствует генеральному плану. "
     "Отказ можно обжаловать в установленном порядке.",
     "Телімнің нысаналы мақсатын өзгерту",
     "Өтінішті қанағаттандырудан бас тартылды: сұралған мақсат бас жоспарға сәйкес келмейді. "
     "Бас тартуға белгіленген тәртіппен шағымдануға болады."),
    ("KZ-2026-101", "Получение участка под ИЖС", "На рассмотрении",
     "Заявление принято в работу. Проверяется наличие свободных участков в вашем населённом пункте.",
     "ЖТС үшін телім алу",
     "Өтініш жұмысқа қабылданды. Елді мекеніңізде бос телімдердің бар-жоғы тексерілуде."),
]


def _pick(props: dict, keys: tuple):
    for k in keys:
        if props.get(k) not in (None, ""):
            return props[k]
    return None


def load_geojson(path: Path) -> int:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    features = data["features"] if data.get("type") == "FeatureCollection" else [data]
    ts = services.now()
    count = 0
    for i, f in enumerate(features, 1):
        geom = f.get("geometry")
        if not geom or geom.get("type") not in ("Polygon", "MultiPolygon", "Point"):
            continue
        props = f.get("properties") or {}
        cad = str(_pick(props, CADASTRAL_KEYS) or f"участок-{i}")
        area = _pick(props, AREA_KEYS)
        try:
            area = float(area) if area is not None else services.area_ha(geom)
        except (TypeError, ValueError):
            area = services.area_ha(geom)
        status = props.get("status") if props.get("status") in services.STATUSES else "ok"
        deadline = None
        if props.get("deadline_days") is not None:
            deadline = (date.today() + timedelta(days=int(props["deadline_days"]))).isoformat()
        pid = db.execute(
            """INSERT INTO parcels(cadastral, purpose, area_ha, geom, status, kind, violation_type,
                                   deadline, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (cad, _pick(props, PURPOSE_KEYS) or "Не указано", area, json.dumps(geom), status, "parcel",
             props.get("violation_type"), deadline, ts, ts),
        )
        if status != "ok":
            services.log_event(pid, f"Статус при загрузке данных: {services.STATUS_LABELS[status]}")
        count += 1
    return count


def _demo_photo(kind: str, seed_n: int) -> str:
    """Рисует схематичное демо-фото (не реальный снимок). Нужно, чтобы карточки в демо не были пустыми."""
    name = f"demo_{kind}_{seed_n}.jpg"
    path = db.UPLOAD_DIR / name
    if path.exists():
        return name
    try:
        import random
        from PIL import Image, ImageDraw
    except ImportError:
        return ""
    rnd = random.Random(seed_n)
    W, H = 640, 420
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    for y in range(H):  # небо и земля
        if y < 160:
            d.line([(0, y), (W, y)], fill=(150 + y // 5, 190 + y // 6, 225))
        else:
            d.line([(0, y), (W, y)], fill=(120 - (y - 160) // 8, 140 - (y - 160) // 10, 70))
    if kind == "dump":
        for _ in range(60):
            x, y = rnd.randint(120, 520), rnd.randint(230, 380)
            w, h = rnd.randint(14, 46), rnd.randint(10, 30)
            c = rnd.choice([(150, 120, 90), (90, 90, 95), (180, 175, 160), (70, 100, 130), (120, 60, 50)])
            d.rectangle([x, y, x + w, y + h], fill=c, outline=(40, 40, 40))
    elif kind == "fence":
        for x in range(60, 600, 34):
            d.rectangle([x, 200, x + 22, 350], fill=(140, 100, 60), outline=(70, 50, 30))
        d.rectangle([40, 240, 620, 254], fill=(110, 80, 50))
        d.rectangle([40, 300, 620, 314], fill=(110, 80, 50))
    else:  # weeds
        for _ in range(420):
            x, y = rnd.randint(0, W), rnd.randint(190, H)
            d.line([(x, y), (x + rnd.randint(-8, 8), y - rnd.randint(14, 44))], fill=(60 + rnd.randint(0, 60), 110 + rnd.randint(0, 60), 40), width=2)
    d.rectangle([0, H - 26, W, H], fill=(20, 30, 40))
    d.text((10, H - 20), "ДЕМО-ИЗОБРАЖЕНИЕ (схема, не реальный снимок)", fill=(230, 230, 230))
    img.save(path, "JPEG", quality=80)
    return name


DEMO_PHOTO_FOR = {"Стихийная свалка": "dump", "Самозахват": "fence", "Неиспользование участка": "weeds",
                  "Нецелевое использование": "fence"}


def _decorate_demo() -> None:
    """Риск «спутникового анализа» для участков и демо-фото для нарушений."""
    ok_rows = db.query("SELECT id, cadastral FROM parcels WHERE status='ok' AND kind='parcel' ORDER BY id")
    for n, r in enumerate(ok_rows):
        base = (zlib.crc32(r["cadastral"].encode()) % 100) / 100
        risk = 0.72 + base * 0.2 if n % 3 == 1 else 0.05 + base * 0.45  # каждый третий — «подозрительный»
        db.execute("UPDATE parcels SET risk=? WHERE id=?", (round(risk, 2), r["id"]))
    for r in db.query("SELECT id, status, violation_type FROM parcels WHERE kind='parcel'"):
        if r["status"] == "ok":
            continue
        kind = DEMO_PHOTO_FOR.get(r["violation_type"] or "", "weeds")
        fn = _demo_photo(kind, r["id"])
        if fn:
            services.add_photo(r["id"], fn, "Демо-фото нарушения", "citizen" if r["status"] == "signal" else "inspector")
        if r["status"] in ("violation", "in_progress", "resolved"):
            flagged = (date.today() - timedelta(days=25 if r["status"] == "resolved" else 12)).isoformat() + " 10:00:00"
            resolved = (date.today() - timedelta(days=10)).isoformat() + " 15:30:00" if r["status"] == "resolved" else None
            db.execute("UPDATE parcels SET flagged_at=?, resolved_at=? WHERE id=?", (flagged, resolved, r["id"]))


def load_applications() -> None:
    ts = services.now()
    for track, subject, status, expl, subject_kk, expl_kk in APPLICATIONS:
        db.execute(
            """INSERT OR REPLACE INTO applications(track, subject, status, explanation, updated_at, subject_kk, explanation_kk)
               VALUES (?,?,?,?,?,?,?)""",
            (track, subject, status, expl, ts, subject_kk, expl_kk),
        )


def seed_if_empty() -> None:
    db.init()
    if db.one("SELECT COUNT(*) AS c FROM parcels")["c"] == 0 and DEFAULT_GEOJSON.exists():
        n = load_geojson(DEFAULT_GEOJSON)
        _decorate_demo()
        print(f"[seed] загружено участков: {n}")
    if db.one("SELECT COUNT(*) AS c FROM applications")["c"] == 0:
        load_applications()


def reset(geojson: Path | None = None) -> None:
    db.drop_all()
    load_geojson(geojson or DEFAULT_GEOJSON)
    _decorate_demo()
    load_applications()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true", help="очистить БД и загрузить данные заново")
    ap.add_argument("--geojson", type=Path, default=None)
    args = ap.parse_args()
    if args.reset:
        reset(args.geojson)
        print("[seed] БД пересоздана")
    else:
        seed_if_empty()
