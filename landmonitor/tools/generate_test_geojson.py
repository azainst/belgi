"""Генерирует фиктивные участки вокруг Тараза -> data/parcels.geojson.
Нужен, только если организаторы не выдали свой GeoJSON."""
import json
import math
import random
from pathlib import Path

random.seed(7)
CENTER = (42.9017, 71.3658)  # lat, lon (Тараз)

PURPOSES = [
    "Земли населённых пунктов — ИЖС",
    "Земли сельскохозяйственного назначения — пашня",
    "Земли промышленности",
    "Земли населённых пунктов — коммерческое использование",
    "Земли запаса",
    "Земли сельскохозяйственного назначения — пастбище",
]

# (смещение км на восток, км на север, статус, тип нарушения, дедлайн в днях от сегодня)
SPOTS = [
    (-1.8, 1.2, "ok", None, None),
    (-0.6, 2.1, "signal", None, None),
    (0.9, 1.6, "violation", "Неиспользование участка", 21),
    (2.2, 0.9, "ok", None, None),
    (2.9, -0.4, "violation", "Самозахват", -4),          # просрочен — для демо
    (1.4, -1.5, "ok", None, None),
    (0.2, -2.3, "in_progress", "Стихийная свалка", 9),
    (-1.3, -1.9, "ok", None, None),
    (-2.6, -0.5, "signal", None, None),
    (-2.2, 0.4, "ok", None, None),
    (0.1, 0.6, "resolved", "Неиспользование участка", -10),
    (3.4, 1.9, "violation", "Нецелевое использование", 40),
    (-0.2, 3.2, "ok", None, None),
    (1.9, 2.7, "ok", None, None),
]


def rect(lat, lon, w_m, h_m, angle):
    pts = []
    for dx, dy in [(-1, -1), (1, -1), (1, 1), (-1, 1)]:
        x, y = dx * w_m / 2, dy * h_m / 2
        xr = x * math.cos(angle) - y * math.sin(angle)
        yr = x * math.sin(angle) + y * math.cos(angle)
        pts.append([round(lon + xr / (111320 * math.cos(math.radians(lat))), 6),
                    round(lat + yr / 110540, 6)])
    pts.append(pts[0])
    return pts


features = []
for i, (ex, nk, status, vtype, days) in enumerate(SPOTS, 1):
    lat = CENTER[0] + nk / 110.54 + random.uniform(-0.002, 0.002)
    lon = CENTER[1] + ex / (111.32 * math.cos(math.radians(CENTER[0]))) + random.uniform(-0.002, 0.002)
    ring = rect(lat, lon, random.uniform(220, 480), random.uniform(180, 380), random.uniform(0, math.pi))
    props = {
        "cadastral_number": f"06-095-{random.randint(10, 99):03d}-{random.randint(100, 999):04d}",
        "purpose": random.choice(PURPOSES),
        "status": status,
    }
    if vtype:
        props["violation_type"] = vtype
    if days is not None:
        props["deadline_days"] = days
    features.append({"type": "Feature", "properties": props,
                     "geometry": {"type": "Polygon", "coordinates": [ring]}})

out = Path(__file__).resolve().parent.parent / "data" / "parcels.geojson"
out.write_text(json.dumps({"type": "FeatureCollection", "features": features}, ensure_ascii=False, indent=1),
               encoding="utf-8")
print(f"OK: {len(features)} участков -> {out}")
