#!/usr/bin/env python3
"""
Конвертер «Факторы_формирования_СПП_*.xlsx» → машиночитаемый реестр факторов.

Источник (лист «Факторы СПП») задаёт 24 фактора и их веса (сумма ровно 100),
лист «Нормализация вилок» — вилки значений и коэффициенты K для тех факторов,
у которых значение динамическое (pH, ЕКО, гумус, Ca:Mg, осадки, температура,
логистика) плюс «оптимумные» строки K=1.0 для остальных.

На выходе два CSV в backend/data/:
  * factors_spp.csv   — реестр факторов (по одной строке на фактор);
  * factors_bands.csv — вилки нормализации (по строке на диапазон).

Запуск:
    python3 backend/scripts/build_factors_registry.py          # перегенерировать
    python3 backend/scripts/build_factors_registry.py --check  # только сверить

Сверка (--check) не пишет файлы и возвращает код 1, если сгенерированное
содержимое разошлось с тем, что лежит в backend/data. Так тест ловит ситуацию
«xlsx обновили, CSV забыли перегенерировать».

Парсинг намеренно «жёсткий»: имена факторов из xlsx сопоставляются с
идентификаторами по точному совпадению строки. Если заказчик переименует или
добавит фактор — скрипт упадёт с понятным сообщением, а не молча потеряет вес.
"""
from __future__ import annotations

import argparse
import csv
import io
import re
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "backend" / "data"
SOURCE_XLSX = DATA_DIR / "Факторы_формирования_СПП.xlsx"

SHEET_FACTORS = "Факторы СПП"
SHEET_BANDS = "Нормализация вилок"

# ---------------------------------------------------------------------------
# Сопоставление «название фактора из xlsx» → стабильный идентификатор в коде.
# Ключ — точная строка из колонки «Фактор» листа «Факторы СПП».
# ---------------------------------------------------------------------------
FACTOR_IDS: dict[str, str] = {
    "История поля и фитосанитарные сроки": "rotation_history",
    "Недопустимый предшественник (Unacceptable)": "predecessor_unacceptable",
    "Хороший предшественник (Good)": "predecessor_good",
    "Допустимый предшественник (Acceptable)": "predecessor_acceptable",
    "Культуры прямого действия навоза КРС": "manure_direct_response",
    "Водородный показатель (pH)": "soil_ph",
    "Емкость катионного обмена (ЕКО / CEC)": "soil_cec",
    "Микробиологическая деструкция СЗР": "microbial_degradation",
    "Содержание гумуса / Органика": "soil_humus",
    "Органическое вещество (Навоз КРС)": "manure_organic_matter",
    "Соотношение Кальция к Магнию (Ca:Mg)": "soil_ca_mg_ratio",
    "Гранулометрический состав почвы": "soil_texture",
    "Макроэлементы (N, P, K)": "soil_npk",
    "Показатель IOFCs (Маржа поля)": "iofc_margin",
    "Пролонгированное NPK-питание (Навоз КРС)": "manure_npk_prolonged",
    "Контрактные обязательства": "contract_obligations",
    "Сумма осадков от внесения до сева": "precipitation_sum",
    "Температурный режим почвы": "soil_temperature_days",
    "Технический флот и рабочие окна": "machinery_fleet",
    "Оборотный капитал (Бюджет)": "working_capital",
    "Мощности хранения и сушки": "storage_drying",
    "Ценность как предшественника": "predecessor_value",
    "Тип обработки почвы": "tillage_type",
    "Логистическое плечо": "logistics_distance",
}

# Названия параметров на листе «Нормализация вилок» отличаются от листа
# «Факторы СПП» — сопоставляем их с теми же идентификаторами.
BAND_PARAM_IDS: dict[str, str] = {
    "Водородный показатель (pH)": "soil_ph",
    "Емкость катионного обмена (ЕКО / CEC)": "soil_cec",
    "Содержание гумуса (Органика)": "soil_humus",
    "Соотношение Кальция к Магнию (Ca:Mg)": "soil_ca_mg_ratio",
    "Сумма осадков от внесения до сева": "precipitation_sum",
    "Температура почвы (> +10°C)": "soil_temperature_days",
    "Логистическое плечо (До элеватора)": "logistics_distance",
    "Хороший предшественник (Good)": "predecessor_good",
    "Культуры прямого действия навоза КРС": "manure_direct_response",
    "Микробиологическая деструкция СЗР": "microbial_degradation",
    "Органическое вещество (Навоз КРС)": "manure_organic_matter",
    "Гранулометрический состав почвы": "soil_texture",
    "Макроэлементы (N, P, K)": "soil_npk",
    "Показатель IOFCs (Маржа поля)": "iofc_margin",
    "Пролонгированное NPK-питание": "manure_npk_prolonged",
    "Контрактные обязательства": "contract_obligations",
    "Технический флот и рабочие окна": "machinery_fleet",
    "Оборотный капитал (Бюджет)": "working_capital",
    "Мощности хранения и сушки": "storage_drying",
    "Ценность как предшественника": "predecessor_value",
    "Тип обработки почвы": "tillage_type",
}

# Роль фактора в алгоритме, приведённая к коду.
#   hard_blocker — жёсткий фильтр, культура исключается;
#   bonus        — прибавка к баллу пары (поле, культура);
#   modifier     — модификатор риска последействия СЗР / скорости распада;
#   objective    — главный экономический драйвер (максимизация);
#   constraint   — линейное ресурсное ограничение.
FACTOR_ROLES: dict[str, str] = {
    "rotation_history": "hard_blocker",
    "predecessor_unacceptable": "hard_blocker",
    "predecessor_good": "bonus",
    "predecessor_acceptable": "bonus",
    "manure_direct_response": "bonus",
    "soil_ph": "modifier",
    "soil_cec": "modifier",
    "microbial_degradation": "modifier",
    "soil_humus": "modifier",
    "manure_organic_matter": "modifier",
    "soil_ca_mg_ratio": "modifier",
    "soil_texture": "modifier",
    "soil_npk": "modifier",
    "iofc_margin": "objective",
    "manure_npk_prolonged": "objective",
    "contract_obligations": "bonus",
    "precipitation_sum": "modifier",
    "soil_temperature_days": "modifier",
    "machinery_fleet": "constraint",
    "working_capital": "constraint",
    "storage_drying": "constraint",
    "predecessor_value": "bonus",
    "tillage_type": "modifier",
    "logistics_distance": "bonus",
}

# Как фактор нормализуется в K ∈ [0, 1]:
#   numeric   — числовые вилки из листа «Нормализация вилок»;
#   numeric_texture — то же, но пороги зависят от гранулометрии;
#   matrix    — значение матрицы совместимости «предшественник → культура»;
#   flag      — булев признак (есть/нет), K = 1.0 либо 0.0;
#   enum      — категориальный справочник значений → K;
#   relative  — непрерывная величина, нормируемая на максимум в группе;
#   none      — нормализация не применяется (жёсткий фильтр).
FACTOR_NORM_KIND: dict[str, str] = {
    "rotation_history": "none",
    "predecessor_unacceptable": "none",
    "predecessor_good": "matrix",
    "predecessor_acceptable": "matrix",
    "manure_direct_response": "flag",
    "soil_ph": "numeric",
    "soil_cec": "numeric",
    "microbial_degradation": "flag",
    "soil_humus": "numeric",
    "manure_organic_matter": "flag",
    "soil_ca_mg_ratio": "numeric_texture",
    "soil_texture": "enum",
    "soil_npk": "relative",
    "iofc_margin": "relative",
    "manure_npk_prolonged": "flag",
    "contract_obligations": "flag",
    "precipitation_sum": "numeric",
    "soil_temperature_days": "numeric",
    "machinery_fleet": "flag",
    "working_capital": "flag",
    "storage_drying": "flag",
    "predecessor_value": "flag",
    "tillage_type": "enum",
    "logistics_distance": "numeric",
}

# Единицы измерения — нужны в документации и для проверки входных данных.
FACTOR_UNITS: dict[str, str] = {
    "soil_ph": "pH",
    "soil_cec": "мг-экв/100 г",
    "soil_humus": "%",
    "soil_ca_mg_ratio": "отношение",
    "precipitation_sum": "% от нормы",
    "soil_temperature_days": "дней с T>+10°C",
    "logistics_distance": "км",
    "manure_organic_matter": "т/га",
    "iofc_margin": "руб/га",
    "soil_npk": "мг/кг",
    "machinery_fleet": "га/сутки",
    "working_capital": "руб",
    "storage_drying": "т/сутки",
}

# Файл с исходными данными, из которого фактор считается. Пустая строка —
# фактор считается из уже имеющихся в системе данных (история севооборота).
FACTOR_DATA_SOURCE: dict[str, str] = {
    "rotation_history": "Севооборот_с_химобработкой.xlsx + вещество_безопасный_интервал.xlsx",
    "predecessor_unacceptable": "compatibility_matrix.csv",
    "predecessor_good": "compatibility_matrix.csv",
    "predecessor_acceptable": "compatibility_matrix.csv",
    "manure_direct_response": "field_manure.csv + crop_colors.csv",
    "soil_ph": "field_soil.csv",
    "soil_cec": "field_soil.csv",
    "microbial_degradation": "field_manure.csv",
    "soil_humus": "field_soil.csv",
    "manure_organic_matter": "field_manure.csv",
    "soil_ca_mg_ratio": "field_soil.csv",
    "soil_texture": "field_soil.csv",
    "soil_npk": "field_soil.csv",
    "iofc_margin": "crop_economics.csv",
    "manure_npk_prolonged": "field_manure.csv",
    "contract_obligations": "contracts.csv",
    "precipitation_sum": "field_climate.csv",
    "soil_temperature_days": "field_climate.csv",
    "machinery_fleet": "farm_resources.csv",
    "working_capital": "farm_resources.csv",
    "storage_drying": "farm_resources.csv",
    "predecessor_value": "Севооборот_с_химобработкой.xlsx + crop_colors.csv",
    "tillage_type": "Севооборот_с_химобработкой.xlsx (Технология выращивания)",
    "logistics_distance": "elevators.csv + fields_catalog.parquet",
}

FACTORS_COLUMNS = [
    "id", "order", "category", "name", "data_type", "role", "role_raw",
    "weight", "norm_kind", "unit", "data_source", "description", "notes",
]
BANDS_COLUMNS = [
    "factor_id", "order", "texture", "raw_range", "lo", "hi", "zone", "k",
    "max_weight", "score",
]

NEG_INF = "-inf"
POS_INF = "inf"


# ---------------------------------------------------------------------------
# Парсинг вилок
# ---------------------------------------------------------------------------

_NUM = r"-?\d+(?:[.,]\d+)?"


def _to_float(txt: str) -> float:
    return float(txt.replace(",", "."))


def parse_range(raw: str) -> tuple[float, float]:
    """
    Разобрать текстовую вилку в полуинтервал [lo, hi).

    Поддерживаются формы: «< 4.5», «> 8.5», «>= 30 мг-экв/100г», «4.5 - 5.4»,
    «50% - 79% от нормы», «0 - 15 км», «< 30 дней».

    Возвращает границы «как написано»; выравнивание разрывов между соседними
    вилками делает close_gaps().
    """
    s = str(raw).strip().replace("–", "-").replace("—", "-")
    m = re.match(rf"^(?:<=|<)\s*({_NUM})", s)
    if m:
        return float("-inf"), _to_float(m.group(1))
    m = re.match(rf"^(?:>=|>)\s*({_NUM})", s)
    if m:
        return _to_float(m.group(1)), float("inf")
    m = re.match(rf"^({_NUM})\s*%?\s*-\s*({_NUM})", s)
    if m:
        return _to_float(m.group(1)), _to_float(m.group(2))
    raise ValueError(f"Не удалось разобрать вилку: {raw!r}")


def close_gaps(bands: list[dict]) -> list[dict]:
    """
    Убрать разрывы между соседними вилками.

    В исходном xlsx вилки записаны «по-человечески» и оставляют дыры: например,
    для pH идут «4.5 - 5.4» и «5.5 - 5.9» — значение 5.45 не попадает никуда.
    Правило: вилки сортируются по нижней границе, и верхняя граница каждой
    приподнимается до нижней границы следующей. Получается разбиение всей
    числовой оси на полуинтервалы [lo, hi) без дыр и пересечений.
    """
    ordered = sorted(bands, key=lambda b: b["lo"])
    for i, band in enumerate(ordered):
        band["hi"] = ordered[i + 1]["lo"] if i + 1 < len(ordered) else float("inf")
    ordered[0]["lo"] = float("-inf")
    return ordered


# Гранулометрические варианты порогов в вилках Ca:Mg.
TEXTURE_KEYS = {
    "Легк.почв": "light",
    "Легк.почв или": "light",
    "Сугл": "medium",
    "Тяж": "heavy",
}


def parse_ca_mg_range(raw: str) -> dict[str, tuple[float, float]]:
    """
    Ca:Mg записан одной строкой с тремя вариантами порогов, например:
    «1.5-2.8 для Легк.почв или 3.0-5.4 Сугл или 4.0-6.0 Тяж» либо
    «<1.5 для Легк.почв или <3.0 для Сугл или <4.0 для Тяж».

    Возвращает {'light': (lo, hi), 'medium': (...), 'heavy': (...)}.
    """
    s = str(raw).strip()
    parts = [p.strip() for p in re.split(r"\s+или\s+", s) if p.strip()]
    if len(parts) != 3:
        raise ValueError(f"Ожидались три варианта порогов Ca:Mg, получено {len(parts)}: {raw!r}")
    out: dict[str, tuple[float, float]] = {}
    for part, texture in zip(parts, ("light", "medium", "heavy")):
        # отбрасываем хвост «для Легк.почв» / «Сугл» / «для Тяж»
        cleaned = re.split(r"\s*(?:для\s+)?(?:Легк\.почв|Сугл|Тяж)", part)[0].strip()
        out[texture] = parse_range(cleaned)
    return out


# ---------------------------------------------------------------------------
# Чтение xlsx
# ---------------------------------------------------------------------------

def read_factors(xlsx: Path) -> list[dict]:
    df = pd.read_excel(xlsx, sheet_name=SHEET_FACTORS, header=None)
    rows: list[dict] = []
    order = 0
    for _, row in df.iterrows():
        name = row.get(2)
        if pd.isna(name) or str(name).strip() in ("", "Фактор"):
            continue
        name = str(name).strip()
        if name not in FACTOR_IDS:
            raise KeyError(
                f"Фактор {name!r} отсутствует в FACTOR_IDS — обновите "
                f"{Path(__file__).name} под новую версию xlsx."
            )
        fid = FACTOR_IDS[name]
        weight_raw = row.get(5)
        try:
            weight = float(weight_raw)
        except (TypeError, ValueError):
            weight = 0.0  # «Фильтр (True/False)» у жёстких блокировок
        order += 1
        rows.append({
            "id": fid,
            "order": order,
            "category": str(row.get(1)).strip(),
            "name": name,
            "data_type": str(row.get(3)).strip(),
            "role": FACTOR_ROLES[fid],
            "role_raw": str(row.get(4)).strip(),
            "weight": weight,
            "norm_kind": FACTOR_NORM_KIND[fid],
            "unit": FACTOR_UNITS.get(fid, ""),
            "data_source": FACTOR_DATA_SOURCE[fid],
            "description": str(row.get(6)).strip(),
            "notes": "" if pd.isna(row.get(7)) else str(row.get(7)).strip(),
        })

    missing = set(FACTOR_IDS.values()) - {r["id"] for r in rows}
    if missing:
        raise ValueError(f"В xlsx не найдены факторы: {sorted(missing)}")

    total = sum(r["weight"] for r in rows)
    if abs(total - 100.0) > 1e-6:
        raise ValueError(f"Сумма весов факторов = {total}, ожидалось 100")
    return rows


def read_bands(xlsx: Path, factors: list[dict]) -> list[dict]:
    df = pd.read_excel(xlsx, sheet_name=SHEET_BANDS, header=None)
    weights = {f["id"]: f["weight"] for f in factors}
    norm_kind = {f["id"]: f["norm_kind"] for f in factors}

    grouped: dict[str, list[dict]] = {}
    seen_params: set[str] = set()
    for _, row in df.iterrows():
        param = row.get(1)
        if pd.isna(param) or str(param).strip() in ("", "Параметр (Фактор)"):
            continue
        # Строки-заголовки листа (название матрицы) не несут коэффициента —
        # содержательная строка вилки всегда имеет K в колонке 4.
        if pd.isna(row.get(4)):
            continue
        param = str(param).strip()
        if param not in BAND_PARAM_IDS:
            raise KeyError(
                f"Параметр {param!r} с листа «{SHEET_BANDS}» отсутствует в "
                f"BAND_PARAM_IDS — обновите {Path(__file__).name}."
            )
        fid = BAND_PARAM_IDS[param]
        seen_params.add(fid)
        raw_range = str(row.get(2)).strip()
        zone = str(row.get(3)).strip()
        k = float(row.get(4))
        max_weight = float(row.get(5))
        if abs(max_weight - weights[fid]) > 1e-6:
            raise ValueError(
                f"«{param}»: вес на листе вилок = {max_weight}, "
                f"а на листе факторов = {weights[fid]}"
            )
        grouped.setdefault(fid, []).append({
            "factor_id": fid, "raw_range": raw_range, "zone": zone,
            "k": k, "max_weight": max_weight,
        })

    weighted = {f["id"] for f in factors if f["weight"] > 0}
    if seen_params != weighted:
        raise ValueError(
            "Расхождение наборов: на листе вилок "
            f"{sorted(seen_params - weighted)} лишние, "
            f"{sorted(weighted - seen_params)} отсутствуют"
        )

    out: list[dict] = []
    for fid, items in grouped.items():
        kind = norm_kind[fid]
        if kind == "numeric":
            for b in items:
                b["lo"], b["hi"] = parse_range(b["raw_range"])
            for i, b in enumerate(close_gaps(items), start=1):
                out.append({**b, "order": i, "texture": ""})
        elif kind == "numeric_texture":
            by_texture: dict[str, list[dict]] = {"light": [], "medium": [], "heavy": []}
            for b in items:
                ranges = parse_ca_mg_range(b["raw_range"])
                for texture, (lo, hi) in ranges.items():
                    by_texture[texture].append({**b, "lo": lo, "hi": hi, "texture": texture})
            for texture, tb in by_texture.items():
                for i, b in enumerate(close_gaps(tb), start=1):
                    out.append({**b, "order": i})
        else:
            # Не числовая вилка: одна строка-«оптимум» с K = 1.0, интервал не задан.
            for i, b in enumerate(items, start=1):
                out.append({**b, "order": i, "texture": "",
                            "lo": float("-inf"), "hi": float("inf")})

    out.sort(key=lambda b: (b["factor_id"], b["texture"], b["order"]))
    for b in out:
        b["score"] = round(b["k"] * b["max_weight"], 4)
    return out


# ---------------------------------------------------------------------------
# Запись CSV
# ---------------------------------------------------------------------------

def _fmt(value) -> str:
    if isinstance(value, float):
        if value == float("-inf"):
            return NEG_INF
        if value == float("inf"):
            return POS_INF
        if value.is_integer():
            return str(int(value))
        return f"{value:g}"
    return str(value)


def to_csv(rows: list[dict], columns: list[str]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({c: _fmt(row.get(c, "")) for c in columns})
    return buf.getvalue()


def build(xlsx: Path = SOURCE_XLSX) -> tuple[str, str]:
    factors = read_factors(xlsx)
    bands = read_bands(xlsx, factors)
    return to_csv(factors, FACTORS_COLUMNS), to_csv(bands, BANDS_COLUMNS)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="только сверить с текущими CSV, ничего не писать")
    parser.add_argument("--xlsx", type=Path, default=SOURCE_XLSX)
    args = parser.parse_args()

    factors_csv, bands_csv = build(args.xlsx)
    targets = {
        DATA_DIR / "factors_spp.csv": factors_csv,
        DATA_DIR / "factors_bands.csv": bands_csv,
    }

    if args.check:
        stale = []
        for path, content in targets.items():
            current = path.read_text(encoding="utf-8") if path.exists() else None
            if current != content:
                stale.append(path.name)
        if stale:
            print("Устарели (перегенерируйте без --check): " + ", ".join(stale))
            return 1
        print("Реестр факторов соответствует xlsx.")
        return 0

    for path, content in targets.items():
        path.write_text(content, encoding="utf-8")
        print(f"Записан {path.relative_to(REPO_ROOT)} "
              f"({len(content.splitlines()) - 1} строк)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
