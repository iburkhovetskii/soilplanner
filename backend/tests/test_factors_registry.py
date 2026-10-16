"""Реестр факторов СПП: целостность, вилки нормализации, синхронность с xlsx."""
import subprocess
import sys
from pathlib import Path

import pytest

from app.core.factors.registry import (
    NORM_NUMERIC,
    NORM_NUMERIC_TEXTURE,
    ROLE_HARD_BLOCKER,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
BUILD_SCRIPT = REPO_ROOT / "backend" / "scripts" / "build_factors_registry.py"

# Веса по категориям из «Факторы СПП». Держим числа явно: если при
# перегенерации из xlsx потеряется строка, суммы по категориям это покажут
# точнее, чем общая сумма 100.
CATEGORY_WEIGHTS = {
    "0. Севооборот и Предшественники": 12.0,
    "1. СЗР и Химия почв": 35.0,
    "2. Экономика": 24.0,
    "3. Климат и Влага": 12.0,
    "4. Ресурсы и Техника": 9.0,
    "5. Агротехника": 6.0,
    "6. Логистика": 2.0,
}


def test_registry_size_and_total_weight(registry):
    assert len(registry) == 24
    assert registry.total_weight == pytest.approx(100.0)


def test_category_weights(registry):
    actual = {
        category: sum(f.weight for f in factors)
        for category, factors in registry.by_category().items()
    }
    assert actual == pytest.approx(CATEGORY_WEIGHTS)


def test_hard_blockers_carry_no_weight(registry):
    blockers = registry.hard_blockers()
    assert {f.id for f in blockers} == {"rotation_history", "predecessor_unacceptable"}
    for factor in blockers:
        # Жёсткий фильтр не должен «торговаться» баллами: у него нет веса.
        assert factor.weight == 0.0
        assert factor.norm_kind == "none"
        assert factor.role == ROLE_HARD_BLOCKER


def test_ids_and_orders_are_unique_and_dense(registry):
    ids = [f.id for f in registry.all]
    orders = sorted(f.order for f in registry.all)
    assert len(set(ids)) == len(ids)
    assert orders == list(range(1, len(registry) + 1))


def test_no_orphan_bands_or_enum_values(registry):
    known = {f.id for f in registry.all}
    for factor in registry.all:
        for band in factor.bands:
            assert band.factor_id in known
            assert band.factor_id == factor.id


@pytest.mark.parametrize("factor_id", [
    "soil_ph", "soil_cec", "soil_humus", "precipitation_sum",
    "soil_temperature_days", "logistics_distance",
])
def test_numeric_bands_cover_axis_without_holes(registry, factor_id):
    """
    В исходном xlsx вилки заданы с разрывами (pH: 5.4→5.5, 5.9→6.0, 7.0→7.1),
    поэтому pH = 5.45 не попадал бы никуда. Генератор реестра смыкает границы —
    проверяем, что вся ось покрыта полуинтервалами [lo, hi) без дыр.
    """
    factor = registry.get(factor_id)
    assert factor.norm_kind == NORM_NUMERIC
    bands = sorted(factor.bands, key=lambda b: b.lo)
    assert bands[0].lo == float("-inf")
    assert bands[-1].hi == float("inf")
    for left, right in zip(bands, bands[1:]):
        assert left.hi == right.lo, f"{factor_id}: разрыв {left.hi} → {right.lo}"


@pytest.mark.parametrize("texture", ["light", "medium", "heavy"])
def test_ca_mg_has_independent_scale_per_texture(registry, texture):
    factor = registry.get("soil_ca_mg_ratio")
    assert factor.norm_kind == NORM_NUMERIC_TEXTURE
    bands = sorted(factor.bands_for(texture), key=lambda b: b.lo)
    assert len(bands) == 5
    assert bands[0].lo == float("-inf")
    assert bands[-1].hi == float("inf")
    for left, right in zip(bands, bands[1:]):
        assert left.hi == right.lo


def test_ca_mg_without_texture_is_no_data(registry):
    """Без гранулометрии пороги Ca:Mg неизвестны — это «нет данных», а не 0.0."""
    factor = registry.get("soil_ca_mg_ratio")
    assert factor.bands_for(None) == ()
    assert factor.k_for_value(5.7) is None
    assert factor.k_for_value(5.7, "medium") == 1.0
    assert factor.k_for_value(5.7, "light") == 0.3


def test_every_band_has_valid_k_and_score(registry):
    for factor in registry.all:
        for band in factor.bands:
            assert 0.0 <= band.k <= 1.0
            assert band.max_weight == factor.weight
            assert band.score == pytest.approx(band.k * band.max_weight, abs=1e-6)


def test_weighted_factors_have_optimum_band(registry):
    """У каждого весомого фактора должна быть зона с K = 1.0 — иначе оптимум недостижим."""
    for factor in registry.weighted():
        if not factor.bands:
            continue
        assert max(b.k for b in factor.bands) == 1.0, factor.id


def test_ph_is_unimodal(registry):
    """pH — не монотонный фактор: K растёт до нейтральной зоны и падает."""
    ks = [b.k for b in sorted(registry.get("soil_ph").bands, key=lambda b: b.lo)]
    assert ks == [0.0, 0.4, 0.7, 1.0, 0.7, 0.3, 0.0]
    peak = ks.index(max(ks))
    assert ks[:peak + 1] == sorted(ks[:peak + 1])
    assert ks[peak:] == sorted(ks[peak:], reverse=True)


@pytest.mark.parametrize("factor_id,expected", [
    ("soil_cec", [0.1, 0.5, 0.8, 1.0]),            # монотонный рост
    ("soil_humus", [0.2, 0.6, 1.0, 1.0]),          # рост с плато
    ("soil_temperature_days", [0.1, 0.4, 0.7, 1.0]),
    ("logistics_distance", [1.0, 0.8, 0.5, 0.1]),  # монотонное падение
])
def test_monotone_factors(registry, factor_id, expected):
    ks = [b.k for b in sorted(registry.get(factor_id).bands, key=lambda b: b.lo)]
    assert ks == expected


@pytest.mark.parametrize("value,expected_k", [
    (-1e9, 0.0), (4.49, 0.0), (4.5, 0.4), (5.45, 0.4), (5.5, 0.7),
    (5.99, 0.7), (6.0, 1.0), (7.0, 1.0), (7.1, 0.7), (8.0, 0.3),
    (8.5, 0.0), (1e9, 0.0),
])
def test_k_for_value_uses_half_open_intervals(registry, value, expected_k):
    assert registry.get("soil_ph").k_for_value(value) == expected_k


@pytest.mark.parametrize("value", [None, "", "н/д", float("nan")])
def test_missing_value_is_none_not_zero(registry, value):
    """«Нет данных» ≠ «худшая зона»: 0.0 занизил бы балл поля без анализа почвы."""
    assert registry.get("soil_ph").k_for_value(value) is None


def test_enum_lookup_is_case_insensitive(registry):
    texture = registry.get("soil_texture")
    assert texture.k_for_enum("Глина") == texture.k_for_enum("глина") == 1.0
    assert texture.k_for_enum(" ПЕСОК ") == 0.2
    assert texture.k_for_enum("чернозём") is None
    assert texture.k_for_enum(None) is None
    assert texture.enum_maps_to("Суглинок") == "medium"
    assert registry.get("tillage_type").k_for_enum("минимальная") == 0.6


def test_generated_csv_matches_source_xlsx():
    """
    Реестр — производная от xlsx заказчика. Тест ловит ситуацию «xlsx обновили,
    CSV забыли перегенерировать».
    """
    result = subprocess.run(
        [sys.executable, str(BUILD_SCRIPT), "--check"],
        capture_output=True, text=True, cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_rebuild_is_deterministic(tmp_path):
    """Повторная генерация даёт байт-в-байт то же — иначе каждый билд шумит в git diff."""
    sys.path.insert(0, str(BUILD_SCRIPT.parent))
    try:
        import build_factors_registry as builder
    finally:
        sys.path.pop(0)
    first = builder.build()
    second = builder.build()
    assert first == second
