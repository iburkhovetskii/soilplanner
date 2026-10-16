"""
Рендер картинки плана (по мотивам local_experiments/exp2_plot_per_farm.py):
для каждого хозяйства — карта полей оптимизированного плана, окраска по культуре,
общая легенда снизу. Геометрия берётся из каталога (реальные шейпы там, где есть,
иначе квадраты). Возвращает PNG-байты.

Используем объектный API matplotlib (Figure), а не pyplot — без глобального
состояния, безопасно при вызове из пула потоков FastAPI.
"""
import io
import math

import matplotlib
matplotlib.use("Agg")
from matplotlib import colormaps
from matplotlib.figure import Figure
import matplotlib.patches as mpatches
from matplotlib.collections import PatchCollection

from app.core.data_loader import load_field_geometries


def _culture_of(row: dict):
    return row.get("culture_unified") or row.get("culture")


def _make_color_map(cultures) -> dict:
    """
    Цвета культур для картинки плана.

    Основной источник — справочник заказчика crop_colors.csv (тот же, что на
    карте в вебе), чтобы печатная карта и экран совпадали по цвету. Если
    справочник недоступен, откатываемся на палитры matplotlib.
    """
    cultures = sorted({c for c in cultures if c})
    try:
        from app.core.crops import get_taxonomy
        taxonomy = get_taxonomy()
        if taxonomy.reference:
            return {c: taxonomy.color(c) for c in cultures}
    except Exception:  # noqa: BLE001 — картинка важнее точности палитры
        pass

    if len(cultures) <= 20:
        cmap = colormaps["tab20"]
        return {c: cmap(i % 20) for i, c in enumerate(cultures)}
    colors = []
    for name in ("tab20", "tab20b", "tab20c"):
        cm = colormaps[name]
        colors.extend(cm(i) for i in range(20))
    return {c: colors[i % len(colors)] for i, c in enumerate(cultures)}


def _draw_panel(ax, rows, geoms, color_map, title):
    patches, colors, lons, lats = [], [], [], []
    n_drawn = 0
    for row in rows:
        polys = geoms.get(row.get("field_id"))
        if not polys:
            continue
        color = color_map.get(_culture_of(row), (0.7, 0.7, 0.7))
        for ring in polys:
            patches.append(mpatches.Polygon(ring, closed=True))
            colors.append(color)
            for lon, lat in ring:
                lons.append(lon)
                lats.append(lat)
            n_drawn += 1
    if not patches:
        ax.axis("off")
        ax.set_title(f"{title}\n(нет геометрий)", fontsize=11)
        return 0
    ax.add_collection(PatchCollection(
        patches, facecolors=colors, edgecolor="white", linewidths=0.25, alpha=0.95))
    pad_lon = max((max(lons) - min(lons)) * 0.03, 0.001)
    pad_lat = max((max(lats) - min(lats)) * 0.03, 0.001)
    ax.set_xlim(min(lons) - pad_lon, max(lons) + pad_lon)
    ax.set_ylim(min(lats) - pad_lat, max(lats) + pad_lat)
    lat_mid = (min(lats) + max(lats)) / 2
    ax.set_aspect(1 / math.cos(math.radians(lat_mid)))
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_linewidth(0.5)
    n_cult = len({_culture_of(r) for r in rows if _culture_of(r)})
    ax.set_title(f"{title}\n{n_drawn} полей, {n_cult} культур",
                 fontsize=11, fontweight="bold")
    return n_drawn


def render_plan_png(plan_rows: list[dict], farm: str | None = None,
                    planning_year: int | None = None) -> bytes:
    """PNG плана: панель на каждое хозяйство (или одно, если farm задан)."""
    geoms = load_field_geometries()

    rows = [r for r in plan_rows if (farm is None or r.get("farm") == farm)]
    if not rows:
        rows = plan_rows

    # Хозяйства плана, по убыванию числа полей
    farms_in_plan: dict[str, int] = {}
    for r in rows:
        f = r.get("farm")
        farms_in_plan[f] = farms_in_plan.get(f, 0) + 1
    farm_list = sorted(farms_in_plan, key=lambda f: -farms_in_plan[f])
    if not farm_list:
        farm_list = [None]

    color_map = _make_color_map(_culture_of(r) for r in rows)

    n = len(farm_list)
    ncols = min(3, n)
    nrows = math.ceil(n / ncols)
    fig = Figure(figsize=(6 * ncols, 6 * nrows))
    axes = fig.subplots(nrows, ncols, squeeze=False)
    axflat = [a for r in axes for a in r]

    for ax, f in zip(axflat, farm_list):
        sub = [r for r in rows if r.get("farm") == f] if f is not None else rows
        _draw_panel(ax, sub, geoms, color_map, str(f) if f else "План")
    for ax in axflat[n:]:
        ax.axis("off")

    used = sorted({_culture_of(r) for r in rows if _culture_of(r)})
    handles = [mpatches.Patch(color=color_map[c], label=c) for c in used]
    ncol = min(8, max(4, (len(used) + 3) // 4))
    fig.legend(handles=handles, loc="lower center", ncol=ncol, fontsize=9,
               frameon=False, bbox_to_anchor=(0.5, 0.005),
               title="Культуры", title_fontsize=10)

    year_txt = f" — план {planning_year}" if planning_year else ""
    title = (farm_list[0] if (n == 1 and farm_list[0]) else "Все хозяйства") + year_txt
    fig.suptitle(title, fontsize=18, fontweight="bold", y=0.995)

    n_legend_rows = math.ceil(len(used) / ncol) + 1
    bottom = min(0.22, 0.04 + n_legend_rows * 0.025)
    fig.tight_layout(rect=[0, bottom, 1, 0.96])

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    return buf.getvalue()
