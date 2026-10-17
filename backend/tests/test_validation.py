"""Валидатор плана: те же правила севооборота, что штрафует солвер."""
import pandas as pd

from app.core.validation import validate_solution
from synthetic import PEA, SUNFLOWER, WHEAT, build_df_sev


def _plan(rows: dict) -> pd.DataFrame:
    return pd.DataFrame([
        {"field_id": fid, "culture_unified": culture, "area_ha": 100.0}
        for fid, culture in rows.items()
    ])


def _validate(plan, df_sev, return_rules, compat_matrix, safe_intervals):
    return validate_solution(
        plan, df_sev, return_rules, compat_matrix, safe_intervals,
        crop_targets={}, planning_year=2027,
    )


def _types(errors):
    return [e["type"] for e in errors]


def test_repeat_beyond_max_consecutive_is_error(
    df_sev, return_rules, compat_matrix, safe_intervals,
):
    # A-03: горох в 2026, горох разрешён один год подряд.
    ok, errors = _validate(_plan({"A-03": PEA}), df_sev,
                           return_rules, compat_matrix, safe_intervals)
    assert not ok
    assert _types(errors) == ["Повтор культуры подряд"]
    assert errors[0]["consecutive_years"] == 2


def test_repeat_within_max_consecutive_is_allowed(
    df_sev, return_rules, compat_matrix, safe_intervals,
):
    # B-02: пшеница в 2026, пшеница разрешена два года подряд.
    ok, errors = _validate(_plan({"B-02": WHEAT}), df_sev,
                           return_rules, compat_matrix, safe_intervals)
    assert ok, errors


def test_third_year_in_a_row_is_error(
    return_rules, compat_matrix, safe_intervals,
):
    df = build_df_sev()
    df.loc[(df["field_id"] == "B-02") & (df["year"] == 2025),
           ["culture", "culture_unified"]] = WHEAT
    ok, errors = _validate(_plan({"B-02": WHEAT}), df,
                           return_rules, compat_matrix, safe_intervals)
    assert not ok
    assert "Повтор культуры подряд" in _types(errors)


def test_early_return_matches_solver_rule(
    df_sev, compat_matrix, safe_intervals,
):
    # Солвер штрафует возврат при 1 < разрыв < years_to_return независимо от
    # max_consecutive; валидатор обязан считать так же.
    rules = {SUNFLOWER: {"years_to_return": 4, "max_consecutive": 2}}
    # A-01: подсолнечник в 2025 → в 2027 разрыв 2 года.
    ok, errors = _validate(_plan({"A-01": SUNFLOWER}), df_sev,
                           rules, compat_matrix, safe_intervals)
    assert not ok
    assert "Ранний возврат" in _types(errors)
