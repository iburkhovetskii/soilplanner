"""
Граф смежности полей для подхода 1.

Строит рёбра между полями, чьи центроиды находятся в радиусе R метров
друг от друга. Все веса рёбер равны 1.0 (MVP, согласно
RECOMMENDATIONS.md).

shapely в системе отсутствует, поэтому всё реализовано на чистом
Python + numpy. Альтернативный режим "touching" (касание границ полей)
реализован как приватная функция `_polygons_touching_meters` ниже,
но НЕ выставляется в публичном API: статистическое исследование
показало, что взвешивание по длине общей границы на MVP не оправдано.
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Iterable

R_EARTH_M = 6_371_000.0


# ---------- геометрия ----------

def _haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Расстояние между двумя точками (lon, lat) в метрах."""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R_EARTH_M * c


def _polygon_bbox(poly: list[tuple[float, float]]) -> tuple[float, float, float, float]:
    lons = [p[0] for p in poly]
    lats = [p[1] for p in poly]
    return (min(lons), min(lats), max(lons), max(lats))


def _polygons_bbox(polys: list[list[tuple[float, float]]]) -> tuple[float, float, float, float]:
    bboxes = [_polygon_bbox(p) for p in polys]
    return (
        min(b[0] for b in bboxes),
        min(b[1] for b in bboxes),
        max(b[2] for b in bboxes),
        max(b[3] for b in bboxes),
    )


def _polygon_centroid(poly: list[tuple[float, float]]) -> tuple[float, float]:
    """Центроид замкнутого полигона по формуле Гаусса (в координатах lon/lat).

    Для небольших полей в WGS84 ошибка несущественна — используем как метку
    положения для расстояний центроид-центроид.
    """
    n = len(poly)
    if n == 0:
        return (0.0, 0.0)
    # Если полигон замкнут — повторение последней точки игнорируем при суммировании
    pts = poly[:-1] if n > 1 and poly[0] == poly[-1] else poly
    n = len(pts)
    if n == 0:
        return (0.0, 0.0)
    if n == 1:
        return pts[0]
    if n == 2:
        return ((pts[0][0] + pts[1][0]) / 2, (pts[0][1] + pts[1][1]) / 2)

    area2 = 0.0
    cx = 0.0
    cy = 0.0
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        cross = x1 * y2 - x2 * y1
        area2 += cross
        cx += (x1 + x2) * cross
        cy += (y1 + y2) * cross
    if area2 == 0:
        # вырожденный — среднее
        return (sum(p[0] for p in pts) / n, sum(p[1] for p in pts) / n)
    cx /= 3 * area2
    cy /= 3 * area2
    return (cx, cy)


def _multi_centroid(polys: list[list[tuple[float, float]]]) -> tuple[float, float]:
    """Для multi-polygon — среднее центроидов (без взвешивания по площади)."""
    cs = [_polygon_centroid(p) for p in polys if len(p) >= 3]
    if not cs:
        # fallback — среднее всех вершин
        all_pts = [pt for p in polys for pt in p]
        if not all_pts:
            return (0.0, 0.0)
        return (sum(p[0] for p in all_pts) / len(all_pts),
                sum(p[1] for p in all_pts) / len(all_pts))
    return (sum(c[0] for c in cs) / len(cs), sum(c[1] for c in cs) / len(cs))


def _bbox_intersect_expanded(
    b1: tuple[float, float, float, float],
    b2: tuple[float, float, float, float],
    tol_deg: float,
) -> bool:
    return not (
        b1[2] + tol_deg < b2[0]
        or b2[2] + tol_deg < b1[0]
        or b1[3] + tol_deg < b2[1]
        or b2[3] + tol_deg < b1[1]
    )


def _polygons_touching_meters(
    polys1: list[list[tuple[float, float]]],
    polys2: list[list[tuple[float, float]]],
    tol_m: float = 30.0,
) -> tuple[bool, float]:
    """[ВНУТРЕННЕЕ] Аппроксимация касания: считаем поля соседями, если есть
    пара точек с границ ближе tol_m метров.

    NB: оставлено для возможных будущих экспериментов. На MVP не используется
    — согласно RECOMMENDATIONS.md, взвешивание по длине общей границы не
    оправдано. Для полноценной поддержки нужен shapely (которого нет в среде).
    """
    bbox1 = _polygons_bbox(polys1)
    bbox2 = _polygons_bbox(polys2)
    tol_deg = tol_m / 100_000.0
    if not _bbox_intersect_expanded(bbox1, bbox2, tol_deg):
        return False, 0.0

    close_count = 0
    pts1 = [pt for p in polys1 for pt in p]
    pts2 = [pt for p in polys2 for pt in p]
    for x1, y1 in pts1:
        for x2, y2 in pts2:
            if abs(x1 - x2) > tol_deg or abs(y1 - y2) > tol_deg:
                continue
            d = _haversine_m(x1, y1, x2, y2)
            if d <= tol_m:
                close_count += 1
                if close_count >= 100:
                    break
        if close_count >= 100:
            break

    if close_count == 0:
        return False, 0.0
    approx_len = close_count * (tol_m / 2.0)
    return True, approx_len


# ---------- основная функция ----------

def build_adjacency_graph(
    geoms: dict[str, list[list[tuple[float, float]]]],
    fields: Iterable[str],
    radius_m: float = 1200.0,
) -> list[tuple[str, str, float]]:
    """Строит граф соседства полей по радиусу центроидов.

    Согласно RECOMMENDATIONS.md (статистическое исследование на 3255 полях):
        - радиус R = 1200 м (диапазон 1000-1500);
        - вес каждого ребра = 1.0 (взвешивание по длине общей границы
          на MVP не оправдано).

    Args:
        geoms: {field_id -> [polygon, ...]} (полигон = список (lon, lat)).
        fields: подмножество полей, для которых строим граф.
        radius_m: радиус центроидов в метрах. По умолчанию 1200.

    Returns:
        Список рёбер (field_i, field_j, weight=1.0). i < j лексикографически.
    """
    fields = [f for f in fields if f in geoms]
    n = len(fields)

    # Центроиды и bbox для каждого поля — считаем один раз
    centroids: dict[str, tuple[float, float]] = {}
    bboxes: dict[str, tuple[float, float, float, float]] = {}
    for fid in fields:
        polys = geoms[fid]
        if not polys:
            continue
        centroids[fid] = _multi_centroid(polys)
        bboxes[fid] = _polygons_bbox(polys)

    fields = [f for f in fields if f in centroids]
    n = len(fields)

    # Spatial bucketing по широте (lat) с шагом ~5 км для уменьшения квадратичных
    # сравнений. На широте ~45° — 1° ≈ 111 км → шаг 0.05° ≈ 5.5 км.
    bucket_deg = 0.05
    cand_radius_m = max(radius_m, 0.0)
    cand_radius_deg = max(cand_radius_m / 100_000.0, 0.005)
    bucket_span = max(1, int(math.ceil(cand_radius_deg / bucket_deg)))

    buckets: dict[tuple[int, int], list[str]] = defaultdict(list)
    for fid in fields:
        lon, lat = centroids[fid]
        bx = int(math.floor(lon / bucket_deg))
        by = int(math.floor(lat / bucket_deg))
        buckets[(bx, by)].append(fid)

    edges: list[tuple[str, str, float]] = []
    seen_pairs: set[tuple[str, str]] = set()

    for fid in fields:
        lon_i, lat_i = centroids[fid]
        bx = int(math.floor(lon_i / bucket_deg))
        by = int(math.floor(lat_i / bucket_deg))
        for dx in range(-bucket_span, bucket_span + 1):
            for dy in range(-bucket_span, bucket_span + 1):
                for fid2 in buckets.get((bx + dx, by + dy), ()):
                    if fid2 == fid:
                        continue
                    a, b = (fid, fid2) if fid < fid2 else (fid2, fid)
                    if (a, b) in seen_pairs:
                        continue

                    d = _haversine_m(*centroids[a], *centroids[b])
                    if d <= radius_m:
                        seen_pairs.add((a, b))
                        edges.append((a, b, 1.0))

    return edges


if __name__ == '__main__':
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from geo_loader import load_field_geometries

    geoms = load_field_geometries()
    sample = list(geoms.keys())[:200]
    edges = build_adjacency_graph(geoms, sample, radius_m=1200)
    print(f'Sample fields: {len(sample)}, edges (radius=1200m): {len(edges)}')
    if edges:
        print('First 5 edges:', edges[:5])
