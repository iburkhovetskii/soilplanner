"""
Балльная оценка пары «поле → культура» по факторам формирования СПП.

Модель прямо повторяет спецификацию заказчика:
  * каждый фактор нормализуется в K ∈ [0, 1], где 1.0 — агрономический оптимум;
  * вклад фактора = K × вес; сумма весов всех факторов равна 100;
  * факторы, для которых нет исходных данных, не занижают балл, а выбывают из
    знаменателя — вместе с ними падает «покрытие факторами», которое считается
    и показывается отдельно.

Итоговый балл = 100 × Σ(K·вес) / Σ(вес по доступным факторам) — то есть «сколько
процентов от достижимого на имеющихся данных набрала эта пара».

Три роли факторов по спецификации обрабатываются по-разному:
  * hard_blocker — не участвует в балле, культура просто исключается
    (см. blocked_cultures);
  * constraint   — линейное ресурсное ограничение (флот, оборотный капитал,
    сушка). В балл пары не входит: это ограничение модели, а не оценка. Пока
    данных нет, ограничения не строятся — их вес показывается отдельной
    строкой в отчёте о покрытии;
  * bonus / modifier / objective — входят в балл.

Отдельный продукт модели — множитель безопасного интервала СЗР
(residue_multiplier): факторы-модификаторы «1. СЗР и Химия почв» и
«3. Климат и Влага» по спецификации управляют скоростью распада гербицидов,
а значит — длиной интервала, на котором культура остаётся заблокированной.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Iterable

import pandas as pd

from app.core.adjacency_graph import _haversine_m, _multi_centroid
from app.core.crops import CropTaxonomy, get_taxonomy
from app.core.factors import constants as C
from app.core.factors.inputs import FactorInputs, get_inputs
from app.core.factors.registry import (
    ROLE_BONUS,
    ROLE_CONSTRAINT,
    ROLE_HARD_BLOCKER,
    ROLE_MODIFIER,
    ROLE_OBJECTIVE,
    Factor,
    FactorRegistry,
    get_registry,
)

logger = logging.getLogger("soil_planner.factors")

# Часовой для кэшей, где None — легальное значение.
_MISSING = object()

# Роли, чьи факторы формируют балл пары «поле → культура».
SCORING_ROLES = frozenset({ROLE_BONUS, ROLE_MODIFIER, ROLE_OBJECTIVE})

# Балл пары, для которой не удалось посчитать ни одного фактора. Нейтральная
# середина: такая пара не выигрывает у посчитанной хорошей и не проигрывает
# посчитанной плохой.
NEUTRAL_SCORE = 50.0

# Факторы, которые по спецификации управляют скоростью распада СЗР.
RESIDUE_CATEGORIES = ("1. СЗР и Химия почв", "3. Климат и Влага")

# Факторы, значение которых зависит от КУЛЬТУРЫ, а не только от поля.
# Это принципиальное разделение: в задаче назначения каждое поле получает
# ровно одну культуру, поэтому фактор, одинаковый для всех культур поля,
# добавляет к цели константу и не может изменить решение — он описывает
# качество поля, а не пригодность конкретной культуры на нём.
# Из 100 баллов спецификации на решение работают только эти 36.
CROP_DEPENDENT_FACTORS = frozenset({
    "predecessor_good",
    "predecessor_acceptable",
    "predecessor_value",
    "manure_direct_response",
    "iofc_margin",
    "contract_obligations",
    "logistics_distance",
})


@dataclass(frozen=True)
class FactorValue:
    """Результат расчёта одного фактора для одной пары «поле → культура»."""
    factor_id: str
    name: str
    category: str
    role: str
    weight: float
    k: float | None
    points: float
    raw: str
    zone: str
    reason: str
    imputed: bool = False   # K замещён средним по полю, а не измерен

    @property
    def available(self) -> bool:
        return self.k is not None and not self.imputed


@dataclass(frozen=True)
class ScoreBreakdown:
    """Балл пары с полной расшифровкой по факторам."""
    field_id: str
    culture: str
    score: float
    points: float
    available_weight: float
    scoring_weight: float
    values: tuple[FactorValue, ...]
    estimated: bool = False  # True — данных нет вообще, взят NEUTRAL_SCORE

    @property
    def coverage(self) -> float:
        """Доля веса, обеспеченная данными (0..1)."""
        return (self.available_weight / self.scoring_weight
                if self.scoring_weight else 0.0)


@dataclass(frozen=True)
class FieldContext:
    """
    Единая база сравнения культур на одном поле.

    Знаменатель балла обязан быть одинаковым для всех культур поля — иначе
    культура, про которую данных меньше, считается по меньшему знаменателю и
    незаслуженно выигрывает. Реальный пример из данных: после пастбища
    «Яр.ячмень» с известной несовместимостью (K = 0) получал 0 баллов, а
    культура, которой просто нет в матрице совместимости, — 100.

    Поэтому набор факторов фиксируется на уровне поля: фактор входит в
    знаменатель, если он посчитан хотя бы для одной культуры-кандидата, а
    пропуски по остальным культурам замещаются средним этого же поля.
    """
    field_id: str
    available: frozenset          # id факторов, входящих в знаменатель
    denominator: float
    mean_k: dict                  # {factor_id: среднее K по известным культурам}
    k_by_culture: dict            # {factor_id: {культура: K}}
    discriminating: frozenset     # факторы, реально различающие культуры


@dataclass
class FieldFacts:
    """Всё, что известно о поле из основных данных севооборота."""
    field_id: str
    farm: str | None = None
    subdivision: str | None = None
    area_ha: float | None = None
    tillage: str | None = None
    history: dict[int, str] | None = None
    centroid: tuple[float, float] | None = None  # (lon, lat)

    def predecessor(self, planning_year: int) -> str | None:
        """Культура предшествующего сезона; при пропуске — последний год до плана."""
        history = self.history or {}
        prev = history.get(planning_year - 1)
        if prev:
            return prev
        earlier = [y for y in history if y is not None and y < planning_year]
        return history[max(earlier)] if earlier else None


def build_field_facts(
    df_sev: pd.DataFrame,
    geometries: dict[str, list] | None = None,
) -> dict[str, FieldFacts]:
    """
    Собрать справку по полям из основного датафрейма севооборота.

    Ожидаются колонки field_id / year / culture_unified / area_ha и —
    опционально — farm / subdivision / «Технология выращивания».
    """
    if df_sev is None or df_sev.empty:
        return {}

    tillage_col = None
    for candidate in ("tillage", "Технология выращивания"):
        if candidate in df_sev.columns:
            tillage_col = candidate
            break

    has_farm = "farm" in df_sev.columns
    has_sub = "subdivision" in df_sev.columns

    facts: dict[str, FieldFacts] = {}
    for field_id, group in df_sev.dropna(subset=["field_id"]).groupby("field_id"):
        key = str(field_id)
        areas = group["area_ha"].dropna() if "area_ha" in group else pd.Series(dtype=float)
        farms = group["farm"].dropna() if has_farm else pd.Series(dtype=object)
        subs = group["subdivision"].dropna() if has_sub else pd.Series(dtype=object)

        tillage = None
        if tillage_col:
            with_tillage = group.dropna(subset=[tillage_col])
            if not with_tillage.empty and "year" in with_tillage.columns:
                latest = with_tillage.sort_values("year").iloc[-1]
                tillage = str(latest[tillage_col])
            elif not with_tillage.empty:
                tillage = str(with_tillage.iloc[-1][tillage_col])

        history: dict[int, str] = {}
        if "year" in group.columns and "culture_unified" in group.columns:
            for year, culture in zip(group["year"], group["culture_unified"]):
                if pd.isna(year) or pd.isna(culture):
                    continue
                history[int(year)] = str(culture)

        facts[key] = FieldFacts(
            field_id=key,
            farm=str(farms.iloc[0]) if len(farms) else None,
            subdivision=str(subs.iloc[0]) if len(subs) else None,
            area_ha=float(areas.iloc[0]) if len(areas) else None,
            tillage=tillage,
            history=history,
        )

    if geometries:
        for key, facts_entry in facts.items():
            polys = geometries.get(key)
            if polys:
                facts_entry.centroid = _multi_centroid(polys)
    return facts


class FactorScorer:
    """
    Считает балл факторов для пар «поле → культура».

    Экземпляр создаётся один раз на прогон: он кэширует справку по полям,
    относительные нормировки (IOFCs) и расстояния до элеваторов.
    """

    def __init__(
        self,
        *,
        planning_year: int,
        field_facts: dict[str, FieldFacts] | None = None,
        df_sev: pd.DataFrame | None = None,
        geometries: dict[str, list] | None = None,
        compat_matrix: dict | None = None,
        registry: FactorRegistry | None = None,
        taxonomy: CropTaxonomy | None = None,
        inputs: FactorInputs | None = None,
        predecessor_mode: str = C.PREDECESSOR_SCORING_MODE,
        candidate_cultures: list | None = None,
    ):
        self.planning_year = int(planning_year)
        self.registry = registry or get_registry()
        self.taxonomy = taxonomy or get_taxonomy()
        self.inputs = inputs or get_inputs()
        self.compat_matrix = compat_matrix or {}
        self.predecessor_mode = predecessor_mode
        self.facts = (field_facts if field_facts is not None
                      else build_field_facts(df_sev, geometries))

        self.scoring_factors: tuple[Factor, ...] = tuple(
            f for f in self.registry.weighted() if f.role in SCORING_ROLES
        )
        self.scoring_weight = sum(f.weight for f in self.scoring_factors)
        self.constraint_weight = sum(
            f.weight for f in self.registry.weighted() if f.role == ROLE_CONSTRAINT
        )
        # Полный вес факторов, которые в принципе способны различать культуры.
        self.crop_dependent_weight = sum(
            f.weight for f in self.scoring_factors
            if f.id in CROP_DEPENDENT_FACTORS
        )

        self.candidate_cultures: tuple[str, ...] = tuple(
            dict.fromkeys(str(c) for c in (candidate_cultures
                                           or self._infer_candidates(df_sev)))
        )

        self._iofc_range: dict[str, tuple[float, float]] = {}
        self._elevator_km: dict[str, float] = {}
        self._residue_cache: dict[str, float] = {}
        self._texture_cache: dict[str, str | None] = {}
        self._context_cache: dict[str, FieldContext] = {}
        self._prepare_relative_scales()

    @staticmethod
    def _infer_candidates(df_sev) -> list:
        """Культуры-кандидаты по умолчанию — однолетние из истории набора."""
        from app.core.helpers import is_perennial
        if df_sev is None or "culture_unified" not in getattr(df_sev, "columns", []):
            return []
        return [str(c) for c in df_sev["culture_unified"].dropna().unique()
                if not is_perennial(c)]

    # ------------------------------------------------------------------
    # Подготовка
    # ------------------------------------------------------------------
    def _prepare_relative_scales(self) -> None:
        """Диапазоны IOFCs по хозяйствам — для относительной нормировки."""
        by_farm: dict[str, list[float]] = {}
        for (farm, _culture), record in self.inputs.economics.items():
            value = record.iofc_rub_ha
            if value is None:
                continue
            by_farm.setdefault(farm, []).append(value)
        for farm, values in by_farm.items():
            self._iofc_range[farm] = (min(values), max(values))

    def _distance_to_elevator_km(self, facts: FieldFacts) -> float | None:
        if not self.inputs.has_elevators or not facts.centroid:
            return None
        cached = self._elevator_km.get(facts.field_id)
        if cached is not None:
            return cached
        lon, lat = facts.centroid
        candidates = [e for e in self.inputs.elevators
                      if not e.farm or e.farm == facts.farm]
        if not candidates:
            candidates = list(self.inputs.elevators)
        best = min(_haversine_m(lon, lat, e.lon, e.lat) for e in candidates) / 1000.0
        self._elevator_km[facts.field_id] = best
        return best

    # ------------------------------------------------------------------
    # Публичное API
    # ------------------------------------------------------------------
    def context(self, field_id) -> FieldContext:
        """
        Единая база сравнения культур на поле (кэшируется).

        Считает K каждого фактора по всем культурам-кандидатам, чтобы
        зафиксировать знаменатель и получить среднее для замещения пропусков.
        """
        key = str(field_id)
        cached = self._context_cache.get(key)
        if cached is not None:
            return cached

        facts = self.facts.get(key) or FieldFacts(field_id=key)
        k_by_culture: dict = {}
        mean_k: dict = {}
        available: set = set()
        discriminating: set = set()

        for factor in self.scoring_factors:
            per_culture = {}
            for culture in self.candidate_cultures:
                k, _raw, _reason = self._k_of(factor, facts, culture)
                per_culture[culture] = k
            known = [k for k in per_culture.values() if k is not None]
            k_by_culture[factor.id] = per_culture
            if known:
                available.add(factor.id)
                mean_k[factor.id] = sum(known) / len(known)
                if len(set(known)) > 1 or len(known) != len(per_culture):
                    discriminating.add(factor.id)

        denominator = sum(f.weight for f in self.scoring_factors
                          if f.id in available)
        context = FieldContext(
            field_id=key, available=frozenset(available), denominator=denominator,
            mean_k=mean_k, k_by_culture=k_by_culture,
            discriminating=frozenset(discriminating),
        )
        self._context_cache[key] = context
        return context

    def score(self, field_id, culture) -> ScoreBreakdown:
        """Балл пары «поле → культура» с расшифровкой по каждому фактору."""
        key, name = str(field_id), str(culture)
        facts = self.facts.get(key) or FieldFacts(field_id=key)
        context = self.context(key)

        values = []
        for factor in self.scoring_factors:
            k, raw, reason = self._k_of(factor, facts, name)
            imputed = False
            if k is None and factor.id in context.available:
                # Данные по фактору на поле есть, но именно для этой культуры
                # значения нет. Замещаем средним по полю: «неизвестно» должно
                # быть серединой, а не автоматической победой.
                k = context.mean_k[factor.id]
                imputed = True
                reason = (reason or "нет значения для этой культуры") + \
                    f"; замещено средним по полю ({k:.2f})"
            values.append(self._value(factor, facts, name, k, raw, reason, imputed))

        points = sum(v.points for v in values)
        if context.denominator > 0:
            score = 100.0 * points / context.denominator
            estimated = False
        else:
            score = NEUTRAL_SCORE
            estimated = True
        return ScoreBreakdown(
            field_id=key, culture=name,
            score=round(score, 2), points=round(points, 4),
            available_weight=context.denominator,
            scoring_weight=self.scoring_weight,
            values=tuple(values), estimated=estimated,
        )

    def score_int(self, field_id, culture) -> int:
        """
        Балл 0..100 целым — в таком виде его ест CP-SAT.

        Быстрый путь: берёт готовые K из базы сравнения поля, без повторного
        вызова обработчиков факторов.
        """
        key, name = str(field_id), str(culture)
        context = self.context(key)
        if context.denominator <= 0:
            return int(round(NEUTRAL_SCORE))

        points = 0.0
        for factor in self.scoring_factors:
            if factor.id not in context.available:
                continue
            per_culture = context.k_by_culture[factor.id]
            k = per_culture.get(name, _MISSING)
            if k is _MISSING:
                # Культура вне набора кандидатов — считаем на месте.
                facts = self.facts.get(key) or FieldFacts(field_id=key)
                k, _raw, _reason = self._k_of(factor, facts, name)
            if k is None:
                k = context.mean_k[factor.id]
            points += k * factor.weight
        return int(round(100.0 * points / context.denominator))

    def score_matrix(self, fields: Iterable, cultures: Iterable) -> dict:
        """Матрица целочисленных баллов {(field_id, culture): score}."""
        cultures = list(cultures)
        return {(str(f), c): self.score_int(f, c)
                for f in fields for c in cultures}

    def authority(self, fields: Iterable | None = None,
                  sample: int = 150) -> float:
        """
        Доля модели, реально работающей на решение (0..1).

        Из 100 баллов спецификации на выбор культуры влияют только 36: остальные
        описывают поле и в задаче назначения добавляют к цели константу. Из этих
        36 сегодня обеспечены данными не все. Отношение «обеспечено / всего
        решающих» — это и есть доля доказательной базы, на которой модель имеет
        право распоряжаться планом. Вес балла в целевой функции домножается на
        неё, поэтому по мере поступления данных балл сам набирает силу, а на
        неполных данных остаётся тай-брейком.
        """
        if self.crop_dependent_weight <= 0:
            return 0.0
        keys = [str(f) for f in (fields if fields is not None else self.facts)]
        if len(keys) > sample:
            step = max(1, len(keys) // sample)
            keys = keys[::step][:sample]
        if not keys:
            return 0.0

        working: set = set()
        for key in keys:
            working |= (self.context(key).discriminating & CROP_DEPENDENT_FACTORS)
        weight = sum(f.weight for f in self.scoring_factors if f.id in working)
        return round(weight / self.crop_dependent_weight, 4)

    def residue_multiplier(self, field_id) -> float:
        """
        Множитель безопасного интервала СЗР для поля.

        1.0 — данных нет либо всё в оптимуме сверх нормы; > 1.0 — распад
        замедлен (щелочная реакция, высокая ЕКО, засуха, холод, No-Till), и
        культура остаётся заблокированной дольше; < 1.0 — распад ускорен
        (навоз, тёплый влажный сезон, вспашка).
        """
        key = str(field_id)
        cached = self._residue_cache.get(key)
        if cached is not None:
            return cached

        facts = self.facts.get(key) or FieldFacts(field_id=key)
        weighted_sum = 0.0
        weight_sum = 0.0
        for factor in self.registry.weighted():
            if factor.role != ROLE_MODIFIER or factor.category not in RESIDUE_CATEGORIES:
                continue
            k, _raw, _reason = self._k_of(factor, facts, culture=None)
            if k is None:
                continue
            weighted_sum += k * factor.weight
            weight_sum += factor.weight

        if weight_sum <= 0:
            multiplier = 1.0
        else:
            k_avg = weighted_sum / weight_sum
            multiplier = C.RESIDUE_MULTIPLIER_MIN + (1.0 - k_avg) * (
                C.RESIDUE_MULTIPLIER_MAX - C.RESIDUE_MULTIPLIER_MIN
            )
        multiplier = round(multiplier, 4)
        self._residue_cache[key] = multiplier
        return multiplier

    def coverage(self) -> dict:
        """
        Отчёт о том, какие факторы обеспечены данными.

        Проверка идёт на выборке полей: фактор считается обеспеченным, если
        хотя бы для одного поля выборки удалось получить K.
        """
        sample_fields = list(self.facts.values())[:200]
        sample_culture = self._sample_culture()

        per_factor = []
        available_weight = 0.0
        for factor in self.registry.all:
            if factor.role == ROLE_HARD_BLOCKER:
                status = "implemented"
                covered = None
            elif factor.role == ROLE_CONSTRAINT:
                status = "not_implemented"
                covered = None
            else:
                hits = sum(
                    1 for f in sample_fields
                    if self._k_of(factor, f, sample_culture)[0] is not None
                )
                covered = (hits / len(sample_fields)) if sample_fields else 0.0
                status = "available" if hits else "no_data"
                if hits:
                    available_weight += factor.weight
            per_factor.append({
                "id": factor.id,
                "name": factor.name,
                "category": factor.category,
                "role": factor.role,
                "weight": factor.weight,
                "norm_kind": factor.norm_kind,
                "unit": factor.unit,
                "data_source": factor.data_source,
                "description": factor.description,
                "status": status,
                "fields_covered": None if covered is None else round(covered, 3),
            })

        return {
            "planning_year": self.planning_year,
            "total_weight": self.registry.total_weight,
            "scoring_weight": self.scoring_weight,
            "constraint_weight": self.constraint_weight,
            "available_weight": available_weight,
            "coverage": round(available_weight / self.scoring_weight, 4)
            if self.scoring_weight else 0.0,
            "sources_present": list(self.inputs.present_files),
            "factors": per_factor,
        }

    def _sample_culture(self) -> str:
        for facts in self.facts.values():
            for year in sorted((facts.history or {}), reverse=True):
                culture = facts.history[year]
                if culture:
                    return culture
        return "Яр.пшеница мягкая"

    # ------------------------------------------------------------------
    # Расчёт отдельных факторов
    # ------------------------------------------------------------------
    def _k_of(self, factor: Factor, facts: FieldFacts, culture: str | None):
        """Сырой расчёт фактора: (K или None, исходное значение, причина пропуска)."""
        handler = getattr(self, f"_k_{factor.id}", None)
        if handler is None:
            return None, "", "расчёт фактора не реализован"
        return handler(factor, facts, culture)

    def _value(self, factor: Factor, facts: FieldFacts, culture: str | None,
               k, raw, reason, imputed: bool = False) -> FactorValue:
        # Агрономическая зона показывается только там, где она действительно
        # определена вилкой. У нечисловых факторов на листе нормализации одна
        # строка-«оптимум», и подставлять её при K = 0 нельзя: получилось бы
        # «Идеальная пара для севооборота» у заведомо плохого предшественника.
        # У замещённого средним значения зоны нет по определению.
        zone = ""
        if k is not None and factor.bands and not imputed:
            if factor.norm_kind in ("numeric", "numeric_texture"):
                try:
                    band = factor.band_for_value(float(raw), self._texture_class(facts))
                except (TypeError, ValueError):
                    band = None
                zone = band.zone if band else ""
            elif k >= 1.0:
                zone = factor.bands[0].zone

        return FactorValue(
            factor_id=factor.id, name=factor.name, category=factor.category,
            role=factor.role, weight=factor.weight, k=k,
            points=(k or 0.0) * factor.weight if k is not None else 0.0,
            raw="" if raw is None else str(raw), zone=zone, reason=reason,
            imputed=imputed,
        )

    def _texture_class(self, facts: FieldFacts) -> str | None:
        cached = self._texture_cache.get(facts.field_id, _MISSING)
        if cached is not _MISSING:
            return cached
        soil = self.inputs.soil_of(facts.field_id)
        value = (self.registry.get("soil_texture").enum_maps_to(soil.texture)
                 if soil and soil.texture else None)
        self._texture_cache[facts.field_id] = value
        return value

    # --- 0. Севооборот и предшественники ------------------------------
    def _predecessor_matrix_value(self, facts: FieldFacts,
                                  culture: str | None) -> float | None:
        if not culture or not self.compat_matrix:
            return None
        predecessor = facts.predecessor(self.planning_year)
        if not predecessor:
            return None
        # Диагональ матрицы (культура по себе же) — значимый сигнал: «пшеница по
        # пшенице» стоит 0.2, «горох по гороху» — 0.0. Не пропускаем её.
        row = self.compat_matrix.get(culture)
        if not row:
            return None
        value = row.get(predecessor)
        if value is None or (isinstance(value, float) and math.isnan(value)):
            return None
        return float(value)

    def _k_predecessor_good(self, factor, facts, culture):
        value = self._predecessor_matrix_value(facts, culture)
        if value is None:
            return None, "", "нет предшественника или пары нет в матрице совместимости"
        predecessor = facts.predecessor(self.planning_year)
        if self.predecessor_mode == "discrete":
            k = 1.0 if value >= C.PREDECESSOR_GOOD_THRESHOLD else 0.0
        else:
            k = max(0.0, min(1.0, value))
        return k, f"{predecessor} → {culture} = {value}", ""

    def _k_predecessor_acceptable(self, factor, facts, culture):
        # Вес 0 по спецификации («Нейтральный пропуск»), считаем ради отчёта.
        return self._k_predecessor_good(factor, facts, culture)

    def _k_manure_direct_response(self, factor, facts, culture):
        if not self.inputs.has_manure:
            return None, "", "нет реестра внесения навоза (field_manure.csv)"
        years = self.inputs.years_since_manure(facts.field_id, self.planning_year)
        if years is None:
            return 0.0, "навоз не вносился", ""
        responsive = self.taxonomy.is_manure_responsive(culture) if culture else False
        k = 1.0 if (years == 0 and responsive) else 0.0
        return k, f"{years + 1}-й год после внесения, отзывчивая={responsive}", ""

    # --- 1. СЗР и химия почв ------------------------------------------
    def _soil_value(self, facts: FieldFacts, attribute: str):
        soil = self.inputs.soil_of(facts.field_id)
        if soil is None:
            return None
        return getattr(soil, attribute)

    def _k_soil_ph(self, factor, facts, culture):
        value = self._soil_value(facts, "ph")
        if value is None:
            return None, "", "нет агрохимии поля (field_soil.csv: ph)"
        return factor.k_for_value(value), value, ""

    def _k_soil_cec(self, factor, facts, culture):
        value = self._soil_value(facts, "cec")
        if value is None:
            return None, "", "нет агрохимии поля (field_soil.csv: cec)"
        return factor.k_for_value(value), value, ""

    def _k_soil_humus(self, factor, facts, culture):
        value = self._soil_value(facts, "humus_percent")
        if value is None:
            return None, "", "нет агрохимии поля (field_soil.csv: humus_percent)"
        return factor.k_for_value(value), value, ""

    def _k_soil_ca_mg_ratio(self, factor, facts, culture):
        value = self._soil_value(facts, "ca_mg_ratio")
        if value is None:
            return None, "", "нет агрохимии поля (field_soil.csv: ca_mg_ratio)"
        texture_class = self._texture_class(facts)
        if not texture_class:
            return None, value, ("вилки Ca:Mg зависят от гранулометрии, "
                                 "а texture в field_soil.csv не заполнен")
        return factor.k_for_value(value, texture_class), value, ""

    def _k_soil_texture(self, factor, facts, culture):
        value = self._soil_value(facts, "texture")
        if not value:
            return None, "", "нет агрохимии поля (field_soil.csv: texture)"
        k = factor.k_for_enum(value)
        if k is None:
            return None, value, f"значение {value!r} отсутствует в factors_enum.csv"
        return k, value, ""

    def _k_soil_npk(self, factor, facts, culture):
        soil = self.inputs.soil_of(facts.field_id)
        if soil is None:
            return None, "", "нет агрохимии поля (field_soil.csv: n/p/k)"
        parts = []
        for element, attribute in (("n", "n_mg_kg"), ("p", "p_mg_kg"), ("k", "k_mg_kg")):
            value = getattr(soil, attribute)
            if value is None:
                continue
            threshold = C.NPK_HIGH_THRESHOLDS[element]
            parts.append(max(0.0, min(1.0, value / threshold)))
        if not parts:
            return None, "", "нет агрохимии поля (field_soil.csv: n/p/k)"
        # Балансу отвечает самый дефицитный элемент — берём минимум.
        return min(parts), f"N={soil.n_mg_kg} P={soil.p_mg_kg} K={soil.k_mg_kg}", ""

    def _k_microbial_degradation(self, factor, facts, culture):
        if not self.inputs.has_manure:
            return None, "", "нет реестра внесения навоза (field_manure.csv)"
        years = self.inputs.years_since_manure(facts.field_id, self.planning_year)
        if years is None or years >= len(C.MANURE_DECAY):
            return 0.0, "навоз не вносился", ""
        return C.MANURE_DECAY[years], f"{years + 1}-й год после внесения", ""

    def _k_manure_organic_matter(self, factor, facts, culture):
        if not self.inputs.has_manure:
            return None, "", "нет реестра внесения навоза (field_manure.csv)"
        years = self.inputs.years_since_manure(
            facts.field_id, self.planning_year, min_rate=C.MANURE_MIN_RATE_T_HA
        )
        if years is None or years >= len(C.MANURE_DECAY):
            return 0.0, f"нет внесения > {C.MANURE_MIN_RATE_T_HA:g} т/га", ""
        return C.MANURE_DECAY[years], f"{years + 1}-й год после внесения", ""

    # --- 2. Экономика --------------------------------------------------
    def _k_iofc_margin(self, factor, facts, culture):
        if not self.inputs.has_economics:
            return None, "", "нет плановой экономики культур (crop_economics.csv)"
        record = self.inputs.economics_of(facts.farm, culture)
        value = record.iofc_rub_ha if record else None
        if value is None:
            return None, "", f"нет строки экономики для «{culture}»"
        low, high = self._iofc_range.get(facts.farm or "", (None, None))
        if low is None:
            low, high = self._iofc_range.get("", (None, None))
        if low is None or high is None or high <= low:
            return 1.0, f"{value:.0f} руб/га", "нормировать не на что: одна культура"
        k = (value - low) / (high - low)
        return max(0.0, min(1.0, k)), f"{value:.0f} руб/га", ""

    def _k_manure_npk_prolonged(self, factor, facts, culture):
        if not self.inputs.has_manure:
            return None, "", "нет реестра внесения навоза (field_manure.csv)"
        years = self.inputs.years_since_manure(facts.field_id, self.planning_year)
        if years is None:
            return 0.0, "навоз не вносился", ""
        k = 1.0 if years in C.MANURE_NPK_YEARS else 0.0
        return k, f"{years + 1}-й год после внесения", ""

    def _k_contract_obligations(self, factor, facts, culture):
        if not self.inputs.has_contracts:
            return None, "", "нет реестра контрактов (contracts.csv)"
        record = self.inputs.contract_of(facts.farm, culture)
        if record and (record.contracted_ha or 0) > 0:
            return 1.0, f"контракт на {record.contracted_ha:g} га", ""
        return 0.0, "контракта нет", ""

    # --- 3. Климат и влага ---------------------------------------------
    def _climate(self, facts: FieldFacts):
        return self.inputs.climate_of(
            self.planning_year, field_id=facts.field_id,
            subdivision=facts.subdivision, farm=facts.farm,
        )

    def _k_precipitation_sum(self, factor, facts, culture):
        record = self._climate(facts)
        value = record.precip_percent_of_norm if record else None
        if value is None:
            return None, "", "нет метеоряда (field_climate.csv: precip_percent_of_norm)"
        return factor.k_for_value(value), value, ""

    def _k_soil_temperature_days(self, factor, facts, culture):
        record = self._climate(facts)
        value = record.biodays_t10 if record else None
        if value is None:
            return None, "", "нет метеоряда (field_climate.csv: biodays_t10)"
        return factor.k_for_value(value), value, ""

    # --- 5. Агротехника -------------------------------------------------
    def _k_predecessor_value(self, factor, facts, culture):
        if not culture:
            return None, "", "культура не задана"
        legume = self.taxonomy.is_legume(culture)
        return (1.0 if legume else 0.0), ("бобовая" if legume else "не бобовая"), ""

    def _k_tillage_type(self, factor, facts, culture):
        if not facts.tillage:
            return None, "", "нет технологии выращивания в истории поля"
        k = factor.k_for_enum(facts.tillage)
        if k is None:
            return None, facts.tillage, (
                f"значение {facts.tillage!r} отсутствует в factors_enum.csv"
            )
        return k, facts.tillage, ""

    # --- 6. Логистика ----------------------------------------------------
    def _k_logistics_distance(self, factor, facts, culture):
        if not self.inputs.has_elevators:
            return None, "", "нет справочника элеваторов (elevators.csv)"
        distance = self._distance_to_elevator_km(facts)
        if distance is None:
            return None, "", "нет геометрии поля — плечо не посчитать"
        if C.LOGISTICS_BULK_ONLY and culture and not self.taxonomy.is_bulk(culture):
            return 1.0, f"{distance:.1f} км", "культура не объёмная, плечо не штрафуется"
        return factor.k_for_value(distance), round(distance, 1), ""


def coverage_report(
    df_sev: pd.DataFrame,
    compat_matrix: dict,
    planning_year: int,
    sample_fields: int = 200,
) -> dict:
    """
    Отчёт «какие факторы обеспечены данными» по выборке полей.

    Строить справку по всем 9 тыс. полей ради отчёта дорого, а результат от
    этого не меняется: доступность источника — свойство всего набора. Берём
    равномерную выборку идентификаторов полей по всему датафрейму.
    """
    ids = pd.Series(df_sev["field_id"].dropna().unique())
    if len(ids) > sample_fields:
        step = max(1, len(ids) // sample_fields)
        ids = ids.iloc[::step].head(sample_fields)
    subset = df_sev[df_sev["field_id"].isin(set(ids))]
    scorer = FactorScorer(
        planning_year=planning_year, df_sev=subset, compat_matrix=compat_matrix,
    )
    return scorer.coverage()


def blocked_cultures(
    scorer: FactorScorer,
    field_id,
    cultures: Iterable[str],
) -> dict[str, str]:
    """
    Жёсткие блокировки по фактору «Недопустимый предшественник».

    Второй жёсткий фактор спецификации — «История поля и фитосанитарные сроки» —
    уже реализован в app.core.chemical (последействие СЗР) и в правилах возврата
    культур внутри солвера, поэтому здесь не дублируется.
    """
    facts = scorer.facts.get(str(field_id))
    if facts is None or not scorer.compat_matrix:
        return {}
    predecessor = facts.predecessor(scorer.planning_year)
    if not predecessor:
        return {}
    blocked: dict[str, str] = {}
    for culture in cultures:
        row = scorer.compat_matrix.get(culture)
        if not row:
            continue
        value = row.get(predecessor)
        if value is None or (isinstance(value, float) and math.isnan(value)):
            continue
        if float(value) <= C.PREDECESSOR_UNACCEPTABLE_VALUE:
            blocked[culture] = (
                f"Недопустимый предшественник: {predecessor} → {culture} "
                f"(матрица совместимости = {value})"
            )
    return blocked
