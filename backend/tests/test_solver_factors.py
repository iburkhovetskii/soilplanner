"""Интеграция балла факторов СПП в CP-SAT: цель, жёсткие запреты, отчётность."""
import pandas as pd
import pytest

from app.core.constants import WEIGHT_ROTATION_ERROR
from app.core.factors import FactorScorer
from app.core.factors.inputs import FactorInputs
from synthetic import FARM_A, FARM_B, MAIZE, PEA, SUNFLOWER, WHEAT


@pytest.fixture
def make_scorer(df_sev, compat_matrix, registry, taxonomy):
    def _make(planning_year=2027):
        return FactorScorer(
            planning_year=planning_year, df_sev=df_sev,
            compat_matrix=compat_matrix, registry=registry,
            taxonomy=taxonomy, inputs=FactorInputs(),
        )
    return _make


def test_plan_rows_carry_score_and_predecessor(solve, make_scorer):
    plan, info = solve(factor_scorer=make_scorer(), weight_factor_score=3)
    assert plan is not None
    for column in ("factor_score", "factor_regret", "predecessor"):
        assert column in plan.columns

    optimized = plan[plan["factor_score"].notna()]
    assert len(optimized) == 9                       # 10 полей минус многолетка
    assert optimized["factor_score"].between(0, 100).all()

    # У многолетки балла нет — иначе средний балл плана поедет вниз.
    perennial = plan.set_index("field_id").loc["A-06"]
    assert perennial["factor_score"] is None or pd.isna(perennial["factor_score"])


def test_score_improves_plan_and_changes_assignments(solve, make_scorer):
    off_plan, off_info = solve(factor_scorer=make_scorer(), weight_factor_score=0)
    on_plan, on_info = solve(factor_scorer=make_scorer(), weight_factor_score=3)

    assert on_info["factor_score_avg"] > off_info["factor_score_avg"]
    assert on_info["factor_regret_total"] <= off_info["factor_regret_total"]

    before = off_plan.set_index("field_id")["culture_unified"]
    after = on_plan.set_index("field_id")["culture_unified"]
    assert (before != after.reindex(before.index)).any(), "план должен измениться"


def test_score_cannot_erode_the_area_programme(solve, make_scorer):
    """
    Главная гарантия двухэтапного решения: балл улучшается только среди
    решений, не худших по севообороту и площадям. Без неё балл при любом
    положительном весе скупал весь мягкий допуск площадей — на реальном
    хозяйстве недобор рос с 59 до 917 га уже при весе 1.
    """
    off_plan, off = solve(factor_scorer=make_scorer(), weight_factor_score=0)
    on_plan, on = solve(factor_scorer=make_scorer(), weight_factor_score=3)

    assert on["two_stage"] is True
    assert off["two_stage"] is False

    base_off = (off["rotation_cost"] + off["deficit_cost"]
                + off["compactness_cost"] + off["adjacency_cost"])
    base_on = (on["rotation_cost"] + on["deficit_cost"]
               + on["compactness_cost"] + on["adjacency_cost"])
    assert base_on <= base_off
    assert on["deficit_total_percent"] <= off["deficit_total_percent"]
    assert on["rotation_errors"] <= off["rotation_errors"]
    assert on["base_objective"] == base_on

    # и при этом балл действительно вырос
    assert on["factor_score_avg"] > off["factor_score_avg"]

    for farm, targets in {"Ферма-А": {WHEAT: 200.0}}.items():
        got = on_plan[(on_plan["farm"] == farm)
                      & (on_plan["culture_unified"] == WHEAT)]["area_ha"].sum()
        assert got >= targets[WHEAT] * 0.90


def test_score_does_not_pull_fields_out_of_the_programme(solve, make_scorer):
    """
    Культура вне производственной программы получает худший балл поля —
    иначе балл уводил план на непрограммные культуры (на реальном хозяйстве
    18 полей / 873 га), оплачивая это недобором площадей.
    """
    targets = {FARM_A: {WHEAT: 200.0, PEA: 100.0},
               FARM_B: {WHEAT: 100.0, PEA: 50.0}}
    plan, _ = solve(targets=targets, factor_scorer=make_scorer(),
                    weight_factor_score=9)
    optimized = plan[plan["factor_score"].notna()]
    programme = {WHEAT, PEA}
    off_programme = optimized[~optimized["culture_unified"].isin(programme)]
    assert off_programme.empty, off_programme["culture_unified"].tolist()


def test_junk_culture_names_are_not_assignable(solve, make_scorer, df_sev):
    """Составные написания из истории не должны попадать в план как культура."""
    frame = df_sev.copy()
    junk = frame[frame["field_id"] == "B-04"].copy()
    junk["culture"] = junk["culture_unified"] = "Пар;Чечевица"
    frame = pd.concat([frame[frame["field_id"] != "B-04"], junk], ignore_index=True)

    allowed = {WHEAT, PEA, SUNFLOWER, MAIZE}

    # С фильтром составное имя не может попасть в план даже на то поле, где
    # оно стоит в истории: B-04 обязано получить настоящую культуру.
    with_filter, _ = solve(df=frame, factor_scorer=make_scorer(),
                           allowed_cultures=allowed)
    assert "Пар;Чечевица" not in set(with_filter["culture_unified"])

    # Но фильтр не должен ломать цели: культура, явно заданная в программе,
    # остаётся назначаемой, даже если её нет в справочнике.
    targets = {FARM_B: {"Пар;Чечевица": 50.0}}
    with_target, _ = solve(df=frame, targets=targets, hard_ratio=0.9,
                           factor_scorer=make_scorer(), allowed_cultures=allowed)
    assert "Пар;Чечевица" in set(with_target["culture_unified"])


def test_score_never_beats_hard_constraints(solve, make_scorer):
    """
    Каким бы большим ни был вес балла, жёсткие ограничения целы: химический
    интервал, «ровно одна культура на поле» и жёсткий минимум по площадям.
    """
    plan, info = solve(
        factor_scorer=make_scorer(),
        weight_factor_score=50,          # потолок API
    )
    assert plan is not None
    # имазетапир 2026 на A-01 закрывает подсолнечник в 2027
    assert plan.set_index("field_id").loc["A-01", "culture_unified"] != SUNFLOWER
    assert plan["field_id"].is_unique
    farm_a = plan[plan["farm"] == FARM_A]
    wheat_area = farm_a[farm_a["culture_unified"] == WHEAT]["area_ha"].sum()
    assert wheat_area >= 200.0 * 0.90


def test_objective_decomposes_into_named_costs(solve, make_scorer):
    plan, info = solve(factor_scorer=make_scorer(), weight_factor_score=3,
                       weight_compactness=0)
    total = (info["rotation_cost"] + info["deficit_cost"]
             + info["compactness_cost"] + info["adjacency_cost"]
             + info["factor_cost"])
    assert info["objective_value"] == total


def test_factor_authority_scales_with_available_evidence(solve, make_scorer):
    """
    Балл распоряжается планом ровно в той пропорции, в какой обеспечен
    данными. На синтетике из 36 решающих баллов работают 12 (хороший
    предшественник 8 + ценность предшественника 4), значит авторитет — треть.
    """
    _, info = solve(factor_scorer=make_scorer(), weight_factor_score=3)
    assert info["factor_authority"] == pytest.approx(12 / 36, abs=1e-3)
    assert info["factor_weight_effective"] == pytest.approx(1.0, abs=1e-3)


def test_factor_cost_scales_with_weight(solve, make_scorer):
    _, one = solve(factor_scorer=make_scorer(), weight_factor_score=1)
    _, three = solve(factor_scorer=make_scorer(), weight_factor_score=3)
    assert three["factor_weight"] == 3
    assert three["factor_cost"] > one["factor_cost"]
    # стоимость = Σ round(вес × авторитет × сожаление) по выбранным парам
    expected = three["factor_weight_effective"] * three["factor_regret_total"]
    assert three["factor_cost"] == pytest.approx(expected, rel=0.15)


def test_scores_are_reported_without_weight(solve, make_scorer):
    """Вес 0 — балл в цель не входит, но по-прежнему считается и показывается."""
    plan, info = solve(factor_scorer=make_scorer(), weight_factor_score=0)
    assert info["factor_score_avg"] is not None
    assert info["factor_cost"] == 0
    assert plan["factor_regret"].notna().any()


def test_no_scorer_keeps_previous_behaviour(solve):
    plan, info = solve()
    assert info["factor_score_avg"] is None
    assert info["factor_cost"] == 0
    assert plan["factor_score"].isna().all()


def test_hard_block_removes_pairs_and_soft_penalty(solve, make_scorer):
    """
    Жёсткий запрет недопустимого предшественника выключает пары «поле →
    культура» и одновременно снимает мягкий штраф за ту же пару, чтобы один
    сигнал не учитывался дважды.
    """
    soft_plan, soft = solve(factor_scorer=make_scorer(),
                            hard_block_bad_predecessor=False)
    hard_plan, hard = solve(factor_scorer=make_scorer(),
                            hard_block_bad_predecessor=True)

    assert soft["hard_blocked_pred_pairs"] == 0
    assert hard["hard_blocked_pred_pairs"] > 0
    assert hard["hard_block_pred_enabled"] is True

    # A-03 в 2026 — горох; горох по гороху запрещён матрицей
    assert hard_plan.set_index("field_id").loc["A-03", "culture_unified"] != PEA
    # A-04 в 2026 — подсолнечник; подсолнечник по подсолнечнику тоже
    assert hard_plan.set_index("field_id").loc["A-04", "culture_unified"] != SUNFLOWER


def test_hard_block_never_closes_all_cultures_for_a_field(solve, make_scorer, df_sev):
    """
    Предохранитель: если запрет закрыл бы полю все культуры, он не применяется —
    иначе модель гарантированно нерешаема.
    """
    frame = df_sev.copy()
    # На A-01 в 2026 ставим культуру, которая по матрице запрещает всё
    frame.loc[(frame["field_id"] == "A-01") & (frame["year"] == 2026),
              ["culture", "culture_unified"]] = "Космос"
    matrix = {c: {"Космос": 0.0} for c in (WHEAT, PEA, SUNFLOWER, MAIZE)}

    from app.core.solver import optimize_rotation_with_chemicals
    from synthetic import build_return_rules, build_safe_intervals, build_crop_targets
    plan, info = optimize_rotation_with_chemicals(
        frame, build_return_rules(), matrix, build_safe_intervals(),
        planning_year=2027, crop_targets=build_crop_targets(),
        hard_ratio=0.9, soft_ratio=1.0, max_time=5, num_workers=1,
        weight_compactness=0, weight_factor_score=0,
        hard_block_bad_predecessor=True,
    )
    assert plan is not None, "предохранитель должен сохранить решаемость"
    assert "A-01" in set(plan["field_id"])


def test_hard_block_accounts_for_chemical_bans(df_sev):
    """
    Предохранитель должен видеть ОБА набора запретов. На A-01 имазетапир
    закрывает подсолнечник; если предшественник закрывает всё остальное,
    жёсткий запрет применять нельзя — иначе полю нечего сеять.
    """
    from app.core.solver import optimize_rotation_with_chemicals
    from synthetic import FARM_A, build_return_rules, build_safe_intervals

    # A-01 в 2026 — пшеница. Матрица закрывает после пшеницы всё, кроме
    # подсолнечника, а подсолнечник на этом поле закрыт имазетапиром.
    matrix = {
        WHEAT: {WHEAT: 0.0}, PEA: {WHEAT: 0.0}, MAIZE: {WHEAT: 0.0},
        SUNFLOWER: {WHEAT: 1.0},
    }
    plan, info = optimize_rotation_with_chemicals(
        df_sev, build_return_rules(), matrix, build_safe_intervals(),
        planning_year=2027, crop_targets={FARM_A: {SUNFLOWER: 100.0}},
        hard_ratio=0.5, soft_ratio=1.0, max_time=5, num_workers=1,
        weight_compactness=0, weight_factor_score=0,
        hard_block_bad_predecessor=True,
    )
    assert plan is not None, "предохранитель должен сохранить решаемость"
    assigned = plan.set_index("field_id").loc["A-01", "culture_unified"]
    # подсолнечник закрыт химией — значит поле получило культуру, которую
    # жёсткий запрет закрыл бы, если бы предохранитель не сработал
    assert assigned != SUNFLOWER
    assert assigned in (WHEAT, PEA, MAIZE)


def test_area_weighting_prefers_large_fields(solve, make_scorer):
    """
    С включённым взвешиванием по площади коэффициенты крупных полей растут,
    но остаются ниже цены ошибки севооборота.
    """
    _, plain = solve(factor_scorer=make_scorer(), weight_factor_score=3)
    _, weighted = solve(factor_scorer=make_scorer(), weight_factor_score=3,
                        factor_area_weighted=True)
    assert weighted["factor_cost"] >= plain["factor_cost"]
    assert weighted["factor_cost"] < WEIGHT_ROTATION_ERROR * 10


