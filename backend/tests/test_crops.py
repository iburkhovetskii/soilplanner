"""Справочник культур: цвета для карты и таксономия."""
import re
from pathlib import Path

import pandas as pd
import pytest

from app.core.constants import CULTURE_MAPPING, NON_TARGET_CATEGORIES
from app.core.crops import (
    FALLBACK_COLOR,
    PLACEHOLDER_COLOR,
    CropInfo,
    CropTaxonomy,
    fallback_color,
    load_crop_reference,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "backend" / "data"
HEX = re.compile(r"^#[0-9A-F]{6}$")

# Культуры, которых в справочнике заказчика нет вовсе. Список — замок от
# регресса: он может только сокращаться, когда заказчик пополнит crop_colors.csv.
KNOWN_GAPS = {"Картофель", "Просо", "Райграс", "Яр.рыжик",
              "Одн.тр.(райгр.одн.+кл.перс.)корм"}


def test_reference_integrity(taxonomy):
    reference = taxonomy.reference
    assert len(reference) == 193
    names = [c.name for c in reference]
    assert len(set(names)) == len(names), "имена в справочнике должны быть уникальны"
    for info in reference:
        assert HEX.match(info.color), f"{info.name}: цвет {info.color!r}"


def test_placeholder_colors_are_repaired(taxonomy):
    """
    В выгрузке 10 строк с белым цветом — на карте они невидимы. Справочник
    подменяет их модальным цветом группы shortName.
    """
    raw = load_crop_reference(str(DATA_DIR / "crop_colors.csv"))
    raw_white = {c.name for c in raw if c.color == PLACEHOLDER_COLOR}
    assert raw_white, "в исходном файле были белые заглушки"
    repaired = {c.name for c in taxonomy.reference if c.color == PLACEHOLDER_COLOR}
    assert repaired < raw_white
    assert taxonomy.color("Кострец") != PLACEHOLDER_COLOR


@pytest.mark.parametrize("culture,expected_rule", [
    ("Яр.пшеница мягкая", "exact"),
    ("яр.пшеница  мягкая", "normalized"),
    ("Яр.пшеница_мягкая", "normalized"),
    ("Люцерна 3 г.ж.корм", "year_of_life"),
    ("Тимофеевка 2 г.ж.корм.", "loose"),
    ("Оз.пшеница мягк.с подс.(мн.травосм.корм)", "prefix"),
    ("Кукуруза силос; Кукуруза зерно", "combination"),
    ("Залеж, лес", "combination"),
])
def test_resolution_rules(taxonomy, culture, expected_rule):
    info = taxonomy.resolve(culture)
    assert info is not None, culture
    assert info.matched_by == expected_rule


def test_year_of_life_forms_share_the_colour(taxonomy):
    """Год жизни не меняет культуру: все люцерны одного цвета."""
    colours = {taxonomy.color(name) for name in (
        "Люцерна 1 г.ж.корм", "Люцерна 2 г.ж.корм", "Люцерна 4 г.ж.корм",
        "Люцерна пр.лет корм",
    )}
    assert len(colours) == 1


def test_ambiguous_names_are_not_guessed(taxonomy):
    """
    «Кукуруза» — это и зерно, и силос, у них разные цвета; «Пастбище» бывает
    на пашне (продуктивное) и естественное (без производства). Угадывать
    нельзя: правило должно отказаться.
    """
    assert taxonomy.resolve("Кукуруза") is None
    assert taxonomy.resolve("пастбище") is None


def test_prefix_rule_refuses_on_colour_conflict():
    reference = (
        CropInfo("Клевер красный", "Клевер", "", "Кормовые", "", "#00B050", "1"),
        CropInfo("Клевер белый", "Клевер", "", "Кормовые", "", "#FF0000", "2"),
    )
    taxonomy = CropTaxonomy(reference)
    assert taxonomy.resolve("Клевер") is None
    assert taxonomy.resolve("Клевер красный").color == "#00B050"


def test_short_names_are_not_matched_by_prefix():
    """Слишком короткая основа не должна цепляться к длинным именам."""
    reference = (CropInfo("Пар сидеральный", "Сидерат", "", "Пар", "", "#242F26", "1"),)
    taxonomy = CropTaxonomy(reference)
    assert taxonomy.resolve("Пар") is None      # длина < 4 — префикс не работает
    assert taxonomy.resolve("Пар сидеральный") is not None
    assert taxonomy.resolve("Сидерат").matched_by == "short_name"


def test_unmatched_falls_back_deterministically(taxonomy):
    unknown = "Марсианский подорожник"
    assert taxonomy.resolve(unknown) is None
    colour = taxonomy.color(unknown)
    assert colour == taxonomy.color(unknown) == fallback_color(unknown)
    reference_colours = {c.color for c in taxonomy.reference}
    assert colour not in reference_colours
    assert fallback_color(None) == FALLBACK_COLOR


def test_palette_contract(taxonomy):
    palette = taxonomy.palette(["Яр.пшеница мягкая", "Яр.пшеница мягкая",
                                None, "Марсианский подорожник"])
    assert set(palette) == {"Яр.пшеница мягкая", "Марсианский подорожник"}
    wheat = palette["Яр.пшеница мягкая"]
    assert wheat["matched"] is True
    assert wheat["canonical"] == "Яр.пшеница мягкая"
    assert wheat["category"] == "Зерновые"
    assert wheat["code_1c"]
    unknown = palette["Марсианский подорожник"]
    assert unknown["matched"] is False
    assert unknown["canonical"] is None
    assert unknown["color"]


@pytest.mark.parametrize("culture,flag,expected", [
    ("Горох", "is_legume", True),
    ("Соя", "is_legume", True),
    ("Люцерна пр.лет корм", "is_legume", True),
    ("Кл.кр.+тим.пр.лет корм", "is_legume", True),
    ("Эсп.+костр.1 г.ж.корм", "is_legume", True),
    ("Гречиха", "is_legume", False),       # крупяная, но не бобовая
    ("Просо", "is_legume", False),
    ("Яр.пшеница мягкая", "is_legume", False),
    ("Кукуруза силос", "is_bulk", True),
    ("Сах.свекла", "is_bulk", True),
    ("Яр.пшеница мягкая", "is_bulk", False),
    ("Кукуруза зерно", "is_manure_responsive", True),
    ("Подсолнечник", "is_manure_responsive", True),
    ("Горох", "is_manure_responsive", False),
    ("Кукуруза зерно", "is_high_moisture", True),
    ("Яр.ячмень", "is_high_moisture", False),
    ("Пар", "is_non_production", True),
    ("Залежь", "is_non_production", True),
    ("Яр.пшеница мягкая", "is_non_production", False),
])
def test_taxonomy_flags(taxonomy, culture, flag, expected):
    assert getattr(taxonomy, flag)(culture) is expected


@pytest.mark.parametrize("flag", ["is_legume", "is_bulk", "is_manure_responsive",
                                  "is_high_moisture", "is_non_production"])
def test_flags_tolerate_empty_input(taxonomy, flag):
    assert getattr(taxonomy, flag)(None) is False
    assert getattr(taxonomy, flag)("") is False


def test_non_target_categories_have_colors(taxonomy):
    """
    Нецелевые категории солвер пишет в план вместо культуры — карта обязана их
    раскрасить, иначе исключённые поля станут серыми пятнами.
    """
    unresolved = [c for c in NON_TARGET_CATEGORIES if taxonomy.resolve(c) is None]
    assert unresolved == []


def test_culture_mapping_targets_resolve(taxonomy):
    """Значения CULTURE_MAPPING — это то, что попадает в план. Пробелы фиксируем списком."""
    gaps = {v for v in set(CULTURE_MAPPING.values()) if taxonomy.resolve(v) is None}
    assert gaps <= KNOWN_GAPS, f"новые непокрытые культуры: {gaps - KNOWN_GAPS}"


@pytest.mark.realdata
def test_coverage_on_real_field_catalog(taxonomy):
    """
    Покрытие на актуальном срезе полей (fields_catalog.parquet, 2024-2026):
    доля площади с известным цветом должна оставаться выше 99%.
    """
    path = DATA_DIR / "fields_catalog.parquet"
    if not path.exists():
        pytest.skip("нет fields_catalog.parquet")
    df = pd.read_parquet(path, columns=["crop", "season", "area_ha"]).dropna(subset=["crop"])
    resolved = {name: taxonomy.resolve(name) is not None
                for name in df["crop"].astype(str).unique()}
    matched_area = df[df["crop"].astype(str).map(resolved)]["area_ha"].sum()
    share = matched_area / df["area_ha"].sum()
    assert share > 0.99, f"покрытие площади {share:.2%}"


@pytest.mark.realdata
def test_resolution_is_cached_and_fast(taxonomy):
    path = DATA_DIR / "fields_catalog.parquet"
    if not path.exists():
        pytest.skip("нет fields_catalog.parquet")
    import time
    names = pd.read_parquet(path, columns=["crop"])["crop"].dropna().astype(str).unique()
    for name in names:
        taxonomy.resolve(name)      # прогрев кэша
    start = time.perf_counter()
    for _ in range(20):
        for name in names:
            taxonomy.resolve(name)
    assert time.perf_counter() - start < 0.5
