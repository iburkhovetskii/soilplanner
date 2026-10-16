"""Классификация культур и целевые площади."""
import pandas as pd
import pytest

from app.core.helpers import extract_crop_targets, is_perennial
from synthetic import PEA, WHEAT, build_df_sev


@pytest.mark.parametrize("culture", [
    "Люцерна 1 г.ж.сем.",
    "Люцерна 2 г.ж.корм",       # раньше считалась однолетней
    "Люцерна 4 г.ж.корм",
    "Кл.кр.+тим.2 г.ж.корм",
    "Эсп.+костр.3 г.ж.корм",
    "Мн.травосм.2 г.ж.корм",
    "Люцерна пр.лет сем.",
    "Залежь",
    "Пастбище естественное",
    "Луг",
    "Не используется",
])
def test_perennial_detected(culture):
    assert is_perennial(culture) is True


@pytest.mark.parametrize("culture", [
    WHEAT, PEA, "Подсолнечник", "Кукуруза силос", "Оз.рапс", "Пар", "Соя",
])
def test_annual_not_marked_perennial(culture):
    assert is_perennial(culture) is False


def test_nan_is_perennial():
    assert is_perennial(float("nan")) is True


def test_multi_year_perennials_are_excluded_from_targets():
    """
    Регресс: травы 2-5 годов жизни не должны попадать в целевые площади —
    поле уже занято, пересевать его в плане нечем.
    """
    df = build_df_sev()
    extra = df[df["field_id"] == "A-06"].copy()
    extra["culture"] = "Люцерна 3 г.ж.корм"
    extra["culture_unified"] = "Люцерна 3 г.ж.корм"
    df = pd.concat([df[df["field_id"] != "A-06"], extra], ignore_index=True)

    targets = extract_crop_targets(df, 2026)
    assert "Люцерна 3 г.ж.корм" not in targets
    assert WHEAT in targets


def test_targets_deduplicate_chemical_rows():
    """На поле несколько строк химобработки — площадь не должна умножаться."""
    df = build_df_sev()
    # Пшеница-2026: A-01 и A-02 по 100 га, B-02 и B-03 по 50 га.
    # У A-01 и B-01 по две строки химобработки — они не должны удвоить площадь.
    assert extract_crop_targets(df, 2026)[WHEAT] == 300.0
    per_farm = extract_crop_targets(df, 2026, per_farm=True)
    assert per_farm["Ферма-А"][WHEAT] == 200.0
    assert per_farm["Ферма-Б"][WHEAT] == 100.0


def test_non_target_categories_are_unique():
    from app.core.constants import NON_TARGET_CATEGORIES
    assert len(set(NON_TARGET_CATEGORIES)) == len(NON_TARGET_CATEGORIES)
