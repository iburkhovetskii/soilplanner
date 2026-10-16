"""
Реестр факторов формирования СПП и вилок нормализации.

Источник истины — «Факторы_формирования_СПП_*.xlsx» заказчика; в репозитории
он лежит как backend/data/Факторы_формирования_СПП.xlsx, а машиночитаемые
factors_spp.csv / factors_bands.csv генерируются из него скриптом
backend/scripts/build_factors_registry.py. Здесь эти CSV только читаются.

Модель нормализации: каждый фактор превращает своё «сырое» значение в
коэффициент K ∈ [0, 1], где 1.0 — агрономический оптимум. Вклад фактора в
итоговый балл = K × вес фактора; сумма весов всех факторов равна ровно 100.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field as dc_field
from functools import lru_cache
from pathlib import Path

from app.config import settings

FACTORS_FILE = "factors_spp.csv"
BANDS_FILE = "factors_bands.csv"
ENUM_FILE = "factors_enum.csv"

# Роли факторов (колонка role в factors_spp.csv)
ROLE_HARD_BLOCKER = "hard_blocker"
ROLE_BONUS = "bonus"
ROLE_MODIFIER = "modifier"
ROLE_OBJECTIVE = "objective"
ROLE_CONSTRAINT = "constraint"

# Способы нормализации (колонка norm_kind)
NORM_NUMERIC = "numeric"
NORM_NUMERIC_TEXTURE = "numeric_texture"
NORM_MATRIX = "matrix"
NORM_FLAG = "flag"
NORM_ENUM = "enum"
NORM_RELATIVE = "relative"
NORM_NONE = "none"


def _to_float(txt: str) -> float:
    t = (txt or "").strip()
    if t in ("-inf", "-Infinity"):
        return float("-inf")
    if t in ("inf", "Infinity", "+inf"):
        return float("inf")
    return float(t)


@dataclass(frozen=True)
class Band:
    """Одна вилка нормализации: полуинтервал [lo, hi) → коэффициент K."""
    factor_id: str
    order: int
    texture: str          # '' для факторов без зависимости от гранулометрии
    raw_range: str
    lo: float
    hi: float
    zone: str
    k: float
    max_weight: float
    score: float

    def contains(self, value: float) -> bool:
        return self.lo <= value < self.hi


@dataclass(frozen=True)
class Factor:
    """Фактор формирования СПП со всеми метаданными из реестра."""
    id: str
    order: int
    category: str
    name: str
    data_type: str
    role: str
    role_raw: str
    weight: float
    norm_kind: str
    unit: str
    data_source: str
    description: str
    notes: str
    bands: tuple[Band, ...] = dc_field(default_factory=tuple)
    # (значение в нижнем регистре, K, maps_to, комментарий)
    enum_map: tuple[tuple[str, float, str, str], ...] = dc_field(default_factory=tuple)

    @property
    def is_hard_blocker(self) -> bool:
        return self.role == ROLE_HARD_BLOCKER

    def bands_for(self, texture: str | None = None) -> tuple[Band, ...]:
        """Вилки фактора; для Ca:Mg — только для указанной гранулометрии."""
        if self.norm_kind != NORM_NUMERIC_TEXTURE:
            return self.bands
        key = (texture or "").strip()
        return tuple(b for b in self.bands if b.texture == key)

    def band_for_value(self, value: float, texture: str | None = None) -> Band | None:
        for band in self.bands_for(texture):
            if band.contains(value):
                return band
        return None

    def k_for_value(self, value, texture: str | None = None) -> float | None:
        """
        K по числовой вилке. None — значение неизвестно либо для этой
        гранулометрии вилок нет (тогда фактор считается «нет данных»).
        """
        if value is None:
            return None
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return None
        band = self.band_for_value(numeric, texture)
        return band.k if band else None

    def k_for_enum(self, value) -> float | None:
        """K по категориальному справочнику factors_enum.csv."""
        row = self._enum_row(value)
        return row[1] if row else None

    def enum_maps_to(self, value) -> str | None:
        """
        Служебная проекция значения enum на другой домен. Используется только
        гранулометрией: значение «суглинок» → класс 'medium', по которому
        выбирается вилка Ca:Mg.
        """
        row = self._enum_row(value)
        return (row[2] or None) if row else None

    def _enum_row(self, value) -> tuple[str, float, str, str] | None:
        if value is None:
            return None
        key = str(value).strip().lower()
        for row in self.enum_map:
            if row[0] == key:
                return row
        return None


class FactorRegistry:
    """Загруженный реестр факторов с быстрым доступом по id и по категории."""

    def __init__(self, factors: list[Factor]):
        self._factors = tuple(sorted(factors, key=lambda f: f.order))
        self._by_id = {f.id: f for f in self._factors}

    def __len__(self) -> int:
        return len(self._factors)

    def __iter__(self):
        return iter(self._factors)

    def __contains__(self, factor_id: object) -> bool:
        return factor_id in self._by_id

    def get(self, factor_id: str) -> Factor:
        try:
            return self._by_id[factor_id]
        except KeyError:
            raise KeyError(f"Неизвестный фактор: {factor_id!r}") from None

    @property
    def all(self) -> tuple[Factor, ...]:
        return self._factors

    @property
    def total_weight(self) -> float:
        return sum(f.weight for f in self._factors)

    def weighted(self) -> tuple[Factor, ...]:
        """Факторы с ненулевым весом (участвуют в балльной оценке)."""
        return tuple(f for f in self._factors if f.weight > 0)

    def hard_blockers(self) -> tuple[Factor, ...]:
        return tuple(f for f in self._factors if f.is_hard_blocker)

    def by_category(self) -> dict[str, list[Factor]]:
        out: dict[str, list[Factor]] = {}
        for f in self._factors:
            out.setdefault(f.category, []).append(f)
        return out


def _read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def load_registry(data_dir: Path | None = None) -> FactorRegistry:
    """Собрать реестр из CSV. Без кэша — удобно для тестов."""
    base = Path(data_dir) if data_dir else settings.data_dir

    bands_by_factor: dict[str, list[Band]] = {}
    for row in _read_csv(base / BANDS_FILE):
        band = Band(
            factor_id=row["factor_id"],
            order=int(row["order"]),
            texture=row["texture"],
            raw_range=row["raw_range"],
            lo=_to_float(row["lo"]),
            hi=_to_float(row["hi"]),
            zone=row["zone"],
            k=float(row["k"]),
            max_weight=float(row["max_weight"]),
            score=float(row["score"]),
        )
        bands_by_factor.setdefault(band.factor_id, []).append(band)

    enum_by_factor: dict[str, list[tuple[str, float, str, str]]] = {}
    enum_path = base / ENUM_FILE
    if enum_path.exists():
        for row in _read_csv(enum_path):
            enum_by_factor.setdefault(row["factor_id"], []).append((
                row["value"].strip().lower(),
                float(row["k"]),
                (row.get("maps_to") or "").strip(),
                row.get("note", ""),
            ))

    factors: list[Factor] = []
    for row in _read_csv(base / FACTORS_FILE):
        fid = row["id"]
        factors.append(Factor(
            id=fid,
            order=int(row["order"]),
            category=row["category"],
            name=row["name"],
            data_type=row["data_type"],
            role=row["role"],
            role_raw=row["role_raw"],
            weight=float(row["weight"]),
            norm_kind=row["norm_kind"],
            unit=row["unit"],
            data_source=row["data_source"],
            description=row["description"],
            notes=row["notes"],
            bands=tuple(sorted(bands_by_factor.get(fid, []),
                               key=lambda b: (b.texture, b.order))),
            enum_map=tuple(enum_by_factor.get(fid, [])),
        ))
    return FactorRegistry(factors)


@lru_cache(maxsize=1)
def get_registry() -> FactorRegistry:
    """Реестр факторов из backend/data (кэшируется на процесс)."""
    return load_registry()
