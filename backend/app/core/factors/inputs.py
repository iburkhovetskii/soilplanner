"""
Опциональные источники данных для факторов СПП.

Часть факторов из спецификации считать пока не из чего: в системе нет
агрохимии полей, метеоряда, экономики и справочника элеваторов. Модуль
описывает контракт на эти данные и грузит их, если файлы появились в
backend/data. Пустой или отсутствующий файл — не ошибка: фактор просто
получает статус «нет данных» и исключается из знаменателя балла.

Шаблоны всех файлов с описанием колонок лежат в backend/data/templates/.
"""
from __future__ import annotations

import csv
import logging
from dataclasses import dataclass, field as dc_field
from functools import lru_cache
from pathlib import Path

from app.config import settings

logger = logging.getLogger("soil_planner.factors")

FIELD_SOIL_FILE = "field_soil.csv"
FIELD_MANURE_FILE = "field_manure.csv"
FIELD_CLIMATE_FILE = "field_climate.csv"
CROP_ECONOMICS_FILE = "crop_economics.csv"
CONTRACTS_FILE = "contracts.csv"
FARM_RESOURCES_FILE = "farm_resources.csv"
ELEVATORS_FILE = "elevators.csv"

OPTIONAL_FILES = (
    FIELD_SOIL_FILE, FIELD_MANURE_FILE, FIELD_CLIMATE_FILE,
    CROP_ECONOMICS_FILE, CONTRACTS_FILE, FARM_RESOURCES_FILE, ELEVATORS_FILE,
)


def _num(value) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", ".")
    if not text or text.lower() in ("nan", "none", "-"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _int(value) -> int | None:
    num = _num(value)
    return int(num) if num is not None else None


def _text(value) -> str:
    return str(value).strip() if value is not None else ""


def _read_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        sample = fh.read(4096)
        fh.seek(0)
        delimiter = ";" if sample.count(";") > sample.count(",") else ","
        return [r for r in csv.DictReader(fh, delimiter=delimiter)
                if any((v or "").strip() for v in r.values())]


@dataclass(frozen=True)
class SoilRecord:
    """Агрохимический паспорт поля (field_soil.csv)."""
    field_id: str
    ph: float | None = None
    cec: float | None = None
    humus_percent: float | None = None
    ca_mg_ratio: float | None = None
    texture: str = ""
    n_mg_kg: float | None = None
    p_mg_kg: float | None = None
    k_mg_kg: float | None = None


@dataclass(frozen=True)
class ManureRecord:
    """Внесение органики (field_manure.csv)."""
    field_id: str
    year: int
    rate_t_ha: float


@dataclass(frozen=True)
class ClimateRecord:
    """
    Метео-срез (field_climate.csv). Привязка — к полю, подразделению или
    хозяйству: чем крупнее гранула, тем грубее, но данные обычно есть только
    по метеостанции хозяйства.
    """
    scope: str          # 'field' | 'subdivision' | 'farm'
    key: str
    year: int
    precip_percent_of_norm: float | None = None
    biodays_t10: float | None = None


@dataclass(frozen=True)
class EconomicsRecord:
    """Плановая экономика культуры (crop_economics.csv). IOFCs = выручка − прямые затраты."""
    farm: str
    culture: str
    yield_t_ha: float | None = None
    price_rub_t: float | None = None
    cost_seeds_rub_ha: float | None = None
    cost_fertilizer_rub_ha: float | None = None
    cost_chemicals_rub_ha: float | None = None

    @property
    def iofc_rub_ha(self) -> float | None:
        if self.yield_t_ha is None or self.price_rub_t is None:
            return None
        costs = sum(c or 0.0 for c in (self.cost_seeds_rub_ha,
                                       self.cost_fertilizer_rub_ha,
                                       self.cost_chemicals_rub_ha))
        return self.yield_t_ha * self.price_rub_t - costs


@dataclass(frozen=True)
class ContractRecord:
    """Форвард/госзаказ (contracts.csv)."""
    farm: str
    culture: str
    contracted_ha: float | None = None


@dataclass(frozen=True)
class FarmResources:
    """Ресурсные потолки хозяйства (farm_resources.csv)."""
    farm: str
    fleet_ha_per_day: float | None = None
    working_capital_rub: float | None = None
    drying_t_per_day: float | None = None


@dataclass(frozen=True)
class Elevator:
    """Точка приёмки (elevators.csv)."""
    name: str
    lat: float
    lon: float
    farm: str = ""


@dataclass
class FactorInputs:
    """Всё, что удалось прочитать. Пустые коллекции — легальное состояние."""
    soil: dict[str, SoilRecord] = dc_field(default_factory=dict)
    manure: dict[str, list[ManureRecord]] = dc_field(default_factory=dict)
    climate: list[ClimateRecord] = dc_field(default_factory=list)
    economics: dict[tuple[str, str], EconomicsRecord] = dc_field(default_factory=dict)
    contracts: dict[tuple[str, str], ContractRecord] = dc_field(default_factory=dict)
    resources: dict[str, FarmResources] = dc_field(default_factory=dict)
    elevators: list[Elevator] = dc_field(default_factory=list)
    present_files: tuple[str, ...] = ()
    _climate_index_cache: dict | None = dc_field(default=None, repr=False, compare=False)

    # --- Доступность источников -------------------------------------------
    @property
    def has_soil(self) -> bool:
        return bool(self.soil)

    @property
    def has_manure(self) -> bool:
        return bool(self.manure)

    @property
    def has_climate(self) -> bool:
        return bool(self.climate)

    @property
    def has_economics(self) -> bool:
        return bool(self.economics)

    @property
    def has_contracts(self) -> bool:
        return bool(self.contracts)

    @property
    def has_resources(self) -> bool:
        return bool(self.resources)

    @property
    def has_elevators(self) -> bool:
        return bool(self.elevators)

    # --- Выборки ----------------------------------------------------------
    def soil_of(self, field_id) -> SoilRecord | None:
        return self.soil.get(str(field_id))

    def manure_of(self, field_id) -> list[ManureRecord]:
        return self.manure.get(str(field_id), [])

    def years_since_manure(self, field_id, planning_year: int,
                           min_rate: float = 0.0) -> int | None:
        """
        Сколько лет прошло с последнего значимого внесения навоза.
        0 — внесли в прошлом сезоне (1-й год последействия). None — не вносили.
        """
        records = [r for r in self.manure_of(field_id)
                   if r.rate_t_ha >= min_rate and r.year < planning_year]
        if not records:
            return None
        return planning_year - max(r.year for r in records) - 1

    def climate_of(self, planning_year: int, *, field_id=None,
                   subdivision=None, farm=None) -> ClimateRecord | None:
        """Самая точная из доступных привязок: поле → подразделение → хозяйство."""
        index = self._climate_index
        for scope, key in (
            ("field", str(field_id) if field_id is not None else None),
            ("subdivision", str(subdivision) if subdivision else None),
            ("farm", str(farm) if farm else None),
        ):
            if not key:
                continue
            record = index.get((scope, key, int(planning_year)))
            if record is not None:
                return record
        return None

    @property
    def _climate_index(self) -> dict[tuple[str, str, int], ClimateRecord]:
        """
        Индекс метеоряда. Метод зовётся на каждую пару «поле → культура»
        (десятки тысяч раз на хозяйство), поэтому линейный поиск по списку
        записей здесь недопустим.
        """
        if self._climate_index_cache is None:
            self._climate_index_cache = {
                (r.scope, r.key, r.year): r for r in self.climate
            }
        return self._climate_index_cache

    def economics_of(self, farm, culture) -> EconomicsRecord | None:
        return (self.economics.get((str(farm or ""), str(culture)))
                or self.economics.get(("", str(culture))))

    def contract_of(self, farm, culture) -> ContractRecord | None:
        return (self.contracts.get((str(farm or ""), str(culture)))
                or self.contracts.get(("", str(culture))))


def load_inputs(data_dir: Path | None = None) -> FactorInputs:
    """Прочитать все опциональные источники. Отсутствие файла — не ошибка."""
    base = Path(data_dir) if data_dir else settings.data_dir
    present: list[str] = []

    soil: dict[str, SoilRecord] = {}
    for row in _read_rows(base / FIELD_SOIL_FILE):
        fid = _text(row.get("field_id"))
        if not fid:
            continue
        soil[fid] = SoilRecord(
            field_id=fid,
            ph=_num(row.get("ph")),
            cec=_num(row.get("cec")),
            humus_percent=_num(row.get("humus_percent")),
            ca_mg_ratio=_num(row.get("ca_mg_ratio")),
            texture=_text(row.get("texture")),
            n_mg_kg=_num(row.get("n_mg_kg")),
            p_mg_kg=_num(row.get("p_mg_kg")),
            k_mg_kg=_num(row.get("k_mg_kg")),
        )
    if soil:
        present.append(FIELD_SOIL_FILE)

    manure: dict[str, list[ManureRecord]] = {}
    for row in _read_rows(base / FIELD_MANURE_FILE):
        fid = _text(row.get("field_id"))
        year = _int(row.get("year"))
        rate = _num(row.get("rate_t_ha"))
        if not fid or year is None or rate is None:
            continue
        manure.setdefault(fid, []).append(ManureRecord(fid, year, rate))
    if manure:
        present.append(FIELD_MANURE_FILE)

    climate: list[ClimateRecord] = []
    for row in _read_rows(base / FIELD_CLIMATE_FILE):
        year = _int(row.get("year"))
        if year is None:
            continue
        for scope, column in (("field", "field_id"),
                              ("subdivision", "subdivision"),
                              ("farm", "farm")):
            key = _text(row.get(column))
            if key:
                climate.append(ClimateRecord(
                    scope=scope, key=key, year=year,
                    precip_percent_of_norm=_num(row.get("precip_percent_of_norm")),
                    biodays_t10=_num(row.get("biodays_t10")),
                ))
                break
    if climate:
        present.append(FIELD_CLIMATE_FILE)

    economics: dict[tuple[str, str], EconomicsRecord] = {}
    for row in _read_rows(base / CROP_ECONOMICS_FILE):
        culture = _text(row.get("culture"))
        if not culture:
            continue
        farm = _text(row.get("farm"))
        economics[(farm, culture)] = EconomicsRecord(
            farm=farm, culture=culture,
            yield_t_ha=_num(row.get("yield_t_ha")),
            price_rub_t=_num(row.get("price_rub_t")),
            cost_seeds_rub_ha=_num(row.get("cost_seeds_rub_ha")),
            cost_fertilizer_rub_ha=_num(row.get("cost_fertilizer_rub_ha")),
            cost_chemicals_rub_ha=_num(row.get("cost_chemicals_rub_ha")),
        )
    if economics:
        present.append(CROP_ECONOMICS_FILE)

    contracts: dict[tuple[str, str], ContractRecord] = {}
    for row in _read_rows(base / CONTRACTS_FILE):
        culture = _text(row.get("culture"))
        if not culture:
            continue
        farm = _text(row.get("farm"))
        contracts[(farm, culture)] = ContractRecord(
            farm=farm, culture=culture,
            contracted_ha=_num(row.get("contracted_ha")),
        )
    if contracts:
        present.append(CONTRACTS_FILE)

    resources: dict[str, FarmResources] = {}
    for row in _read_rows(base / FARM_RESOURCES_FILE):
        farm = _text(row.get("farm"))
        if not farm:
            continue
        resources[farm] = FarmResources(
            farm=farm,
            fleet_ha_per_day=_num(row.get("fleet_ha_per_day")),
            working_capital_rub=_num(row.get("working_capital_rub")),
            drying_t_per_day=_num(row.get("drying_t_per_day")),
        )
    if resources:
        present.append(FARM_RESOURCES_FILE)

    elevators: list[Elevator] = []
    for row in _read_rows(base / ELEVATORS_FILE):
        lat, lon = _num(row.get("lat")), _num(row.get("lon"))
        if lat is None or lon is None:
            continue
        elevators.append(Elevator(name=_text(row.get("name")), lat=lat, lon=lon,
                                  farm=_text(row.get("farm"))))
    if elevators:
        present.append(ELEVATORS_FILE)

    if present:
        logger.info("Источники факторов СПП: подключены %s", ", ".join(present))
    else:
        logger.info(
            "Источники факторов СПП: ни одного дополнительного файла нет — "
            "балл считается по данным севооборота (см. docs/FACTORS_SPP.md)"
        )
    return FactorInputs(
        soil=soil, manure=manure, climate=climate, economics=economics,
        contracts=contracts, resources=resources, elevators=elevators,
        present_files=tuple(present),
    )


@lru_cache(maxsize=1)
def get_inputs() -> FactorInputs:
    """Опциональные источники из backend/data (кэшируется на процесс)."""
    return load_inputs()
