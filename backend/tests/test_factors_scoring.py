"""Балльная оценка пары «поле → культура» и множитель последействия СЗР."""
import pytest

from app.core.factors import FactorScorer, blocked_cultures, coverage_report
from app.core.factors.inputs import FactorInputs, load_inputs
from app.core.factors.scoring import NEUTRAL_SCORE
from synthetic import MAIZE, PEA, SUNFLOWER, WHEAT, build_df_sev, write_soil


def _scorer(mini_registry, df_sev, compat_matrix, taxonomy, inputs=None, **kw):
    return FactorScorer(
        planning_year=2027, df_sev=df_sev, compat_matrix=compat_matrix,
        registry=mini_registry, taxonomy=taxonomy,
        inputs=inputs if inputs is not None else FactorInputs(), **kw,
    )


# --- нормализация и пересчёт весов -------------------------------------------

def test_partial_data_renormalizes_instead_of_penalising(
    mini_registry, df_sev, compat_matrix, taxonomy, tmp_path
):
    """
    Мини-реестр: pH весит 60, «ценность как предшественника» — 40.
    Если известен только предшественник, балл считается по 40 доступным баллам,
    а не по 100: бобовая культура даёт 100, небобовая — 0.
    """
    scorer = _scorer(mini_registry, df_sev, compat_matrix, taxonomy)
    pea = scorer.score("A-01", PEA)
    wheat = scorer.score("A-01", WHEAT)

    assert pea.available_weight == 40.0
    assert pea.scoring_weight == 100.0
    assert pea.coverage == pytest.approx(0.4)
    assert pea.score == 100.0
    assert wheat.score == 0.0


def test_soil_data_joins_the_score(
    mini_registry, df_sev, compat_matrix, taxonomy, tmp_path
):
    """С появлением field_soil.csv фактор pH подключается сам, покрытие растёт."""
    write_soil(tmp_path, {"A-01": {"ph": 6.5}})       # K = 1.0, оптимум
    scorer = _scorer(mini_registry, df_sev, compat_matrix, taxonomy,
                     inputs=load_inputs(tmp_path))

    wheat = scorer.score("A-01", WHEAT)
    assert wheat.available_weight == 100.0
    assert wheat.coverage == 1.0
    # pH: 1.0 × 60, предшественник: 0.0 × 40 → 60 из 100
    assert wheat.score == 60.0

    pea = scorer.score("A-01", PEA)
    assert pea.score == 100.0


def test_score_bounds_and_extremes(
    mini_registry, df_sev, compat_matrix, taxonomy, tmp_path
):
    write_soil(tmp_path, {"A-01": {"ph": 4.0}, "A-03": {"ph": 6.5}})
    scorer = _scorer(mini_registry, df_sev, compat_matrix, taxonomy,
                     inputs=load_inputs(tmp_path))
    worst = scorer.score("A-01", WHEAT)       # pH K=0, небобовая
    best = scorer.score("A-03", PEA)          # pH K=1, бобовая
    assert worst.score == 0.0
    assert best.score == 100.0
    for field_id in ("A-01", "A-02", "A-03"):
        for culture in (WHEAT, PEA, SUNFLOWER, MAIZE):
            assert 0.0 <= scorer.score(field_id, culture).score <= 100.0


def test_no_data_at_all_gives_neutral_score(taxonomy, tmp_path):
    """
    Реестр из одного фактора-агрохимии, а агрохимии нет: считать нечего.
    Балл — нейтральная середина, а не 0: иначе неизвестная пара проигрывала бы
    заведомо плохой.
    """
    from app.core.factors.registry import load_registry
    (tmp_path / "factors_spp.csv").write_text(
        "id,order,category,name,data_type,role,role_raw,weight,norm_kind,unit,"
        "data_source,description,notes\n"
        "soil_ph,1,1. СЗР и Химия почв,pH,Float,modifier,Модификатор,100,"
        "numeric,pH,field_soil.csv,тест,\n",
        encoding="utf-8",
    )
    (tmp_path / "factors_bands.csv").write_text(
        "factor_id,order,texture,raw_range,lo,hi,zone,k,max_weight,score\n"
        "soil_ph,1,,любой,-inf,inf,Всё,1,100,100\n",
        encoding="utf-8",
    )
    scorer = FactorScorer(
        planning_year=2027, field_facts={}, compat_matrix={},
        registry=load_registry(data_dir=tmp_path), taxonomy=taxonomy,
        inputs=FactorInputs(),
    )
    breakdown = scorer.score("нет-такого-поля", WHEAT)
    assert breakdown.estimated is True
    assert breakdown.score == NEUTRAL_SCORE
    assert breakdown.available_weight == 0.0
    assert breakdown.coverage == 0.0


def test_score_int_is_in_cpsat_domain(scorer):
    for culture in (WHEAT, PEA, SUNFLOWER, MAIZE):
        value = scorer.score_int("A-01", culture)
        assert isinstance(value, int)
        assert 0 <= value <= 100


def test_score_matrix_covers_all_pairs(scorer):
    matrix = scorer.score_matrix(["A-01", "A-02"], [WHEAT, PEA])
    assert set(matrix) == {("A-01", WHEAT), ("A-01", PEA),
                           ("A-02", WHEAT), ("A-02", PEA)}
    assert all(isinstance(v, int) for v in matrix.values())


# --- отдельные факторы --------------------------------------------------------

def test_predecessor_factor_reads_matrix(scorer):
    """A-01 в 2026 — пшеница; горох после пшеницы = 1.0, пшеница по пшенице = 0.2."""
    values = {v.factor_id: v for v in scorer.score("A-01", PEA).values}
    assert values["predecessor_good"].k == 1.0
    values = {v.factor_id: v for v in scorer.score("A-01", WHEAT).values}
    assert values["predecessor_good"].k == pytest.approx(0.2)


def test_predecessor_discrete_mode(df_sev, compat_matrix, registry, taxonomy):
    """В дискретном режиме спецификации бонус даёт только «Good» (значение 1.0)."""
    graded = FactorScorer(
        planning_year=2027, df_sev=df_sev, compat_matrix=compat_matrix,
        registry=registry, taxonomy=taxonomy, inputs=FactorInputs(),
    )
    discrete = FactorScorer(
        planning_year=2027, df_sev=df_sev, compat_matrix=compat_matrix,
        registry=registry, taxonomy=taxonomy, inputs=FactorInputs(),
        predecessor_mode="discrete",
    )
    by_id = lambda b: {v.factor_id: v for v in b.values}  # noqa: E731
    assert by_id(graded.score("A-01", SUNFLOWER))["predecessor_good"].k == pytest.approx(0.7)
    assert by_id(discrete.score("A-01", SUNFLOWER))["predecessor_good"].k == 0.0
    assert by_id(discrete.score("A-01", PEA))["predecessor_good"].k == 1.0


def test_tillage_factor_uses_history(scorer):
    values = {v.factor_id: v for v in scorer.score("A-01", WHEAT).values}
    assert values["tillage_type"].k == 1.0            # классическая с плугом
    values = {v.factor_id: v for v in scorer.score("A-03", WHEAT).values}
    assert values["tillage_type"].k == pytest.approx(0.3)   # No-Till
    values = {v.factor_id: v for v in scorer.score("A-04", WHEAT).values}
    assert values["tillage_type"].k is None           # технология не указана
    assert "нет технологии" in values["tillage_type"].reason


def test_legume_factor(scorer):
    for culture, expected in ((PEA, 1.0), (WHEAT, 0.0), (SUNFLOWER, 0.0)):
        values = {v.factor_id: v for v in scorer.score("A-01", culture).values}
        assert values["predecessor_value"].k == expected


def test_factors_without_sources_report_the_missing_file(scorer):
    values = {v.factor_id: v for v in scorer.score("A-01", WHEAT).values}
    assert values["soil_ph"].k is None
    assert "field_soil.csv" in values["soil_ph"].reason
    assert values["iofc_margin"].k is None
    assert "crop_economics.csv" in values["iofc_margin"].reason
    assert values["logistics_distance"].k is None
    assert "elevators.csv" in values["logistics_distance"].reason


def test_manure_factors_activate_with_register(
    df_sev, compat_matrix, registry, taxonomy, tmp_path
):
    (tmp_path / "field_manure.csv").write_text(
        "field_id,year,rate_t_ha\nA-01,2026,45\n", encoding="utf-8"
    )
    scorer = FactorScorer(
        planning_year=2027, df_sev=df_sev, compat_matrix=compat_matrix,
        registry=registry, taxonomy=taxonomy, inputs=load_inputs(tmp_path),
    )
    # 1-й год последействия: кукуруза отзывчива на прямое внесение, пшеница нет
    maize = {v.factor_id: v for v in scorer.score("A-01", MAIZE).values}
    wheat = {v.factor_id: v for v in scorer.score("A-01", WHEAT).values}
    assert maize["manure_direct_response"].k == 1.0
    assert wheat["manure_direct_response"].k == 0.0
    assert maize["microbial_degradation"].k == 1.0
    assert maize["manure_organic_matter"].k == 1.0
    # поле без внесения: реестр есть, значит фактор посчитан и равен нулю
    other = {v.factor_id: v for v in scorer.score("A-02", MAIZE).values}
    assert other["manure_direct_response"].k == 0.0
    assert other["microbial_degradation"].k == 0.0


def test_economics_factor_normalises_within_farm(
    df_sev, compat_matrix, registry, taxonomy, tmp_path
):
    (tmp_path / "crop_economics.csv").write_text(
        "farm,culture,yield_t_ha,price_rub_t,cost_seeds_rub_ha,"
        "cost_fertilizer_rub_ha,cost_chemicals_rub_ha\n"
        f"Ферма-А,{WHEAT},3,10000,2000,5000,3000\n"      # IOFC = 20 000
        f"Ферма-А,{PEA},2,20000,3000,2000,2000\n"        # IOFC = 33 000 (максимум)
        f"Ферма-А,{SUNFLOWER},1,15000,4000,4000,2000\n", # IOFC = 5 000 (минимум)
        encoding="utf-8",
    )
    scorer = FactorScorer(
        planning_year=2027, df_sev=df_sev, compat_matrix=compat_matrix,
        registry=registry, taxonomy=taxonomy, inputs=load_inputs(tmp_path),
    )
    k = lambda c: {v.factor_id: v for v in scorer.score("A-01", c).values}["iofc_margin"].k  # noqa: E731
    assert k(PEA) == 1.0
    assert k(SUNFLOWER) == 0.0
    assert k(WHEAT) == pytest.approx((20000 - 5000) / (33000 - 5000))


def test_climate_binding_prefers_the_narrowest_scope(tmp_path):
    """Поле точнее подразделения, подразделение точнее хозяйства."""
    (tmp_path / "field_climate.csv").write_text(
        "field_id,subdivision,farm,year,precip_percent_of_norm,biodays_t10\n"
        "A-01,,,2027,110,95\n"
        ",Отд-А1,,2027,60,40\n"
        ",,Ферма-А,2027,30,20\n",
        encoding="utf-8",
    )
    inputs = load_inputs(tmp_path)
    narrow = inputs.climate_of(2027, field_id="A-01", subdivision="Отд-А1", farm="Ферма-А")
    assert narrow.precip_percent_of_norm == 110
    mid = inputs.climate_of(2027, field_id="A-99", subdivision="Отд-А1", farm="Ферма-А")
    assert mid.precip_percent_of_norm == 60
    wide = inputs.climate_of(2027, field_id="A-99", subdivision="Отд-Х", farm="Ферма-А")
    assert wide.precip_percent_of_norm == 30
    assert inputs.climate_of(2028, field_id="A-01") is None


def test_climate_lookup_is_indexed_not_linear(tmp_path):
    """
    Поиск метео зовётся на каждую пару «поле → культура» — десятки тысяч раз
    на хозяйство. Линейный проход по записям здесь недопустим.
    """
    import time
    rows = ["field_id,subdivision,farm,year,precip_percent_of_norm,biodays_t10"]
    rows += [f"F-{i},,,2027,{80 + i % 40},{60 + i % 30}" for i in range(20_000)]
    (tmp_path / "field_climate.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    inputs = load_inputs(tmp_path)
    inputs.climate_of(2027, field_id="F-0")          # прогрев индекса
    start = time.perf_counter()
    for i in range(5_000):
        assert inputs.climate_of(2027, field_id=f"F-{i}") is not None
    assert time.perf_counter() - start < 0.5


def test_zone_is_empty_when_band_does_not_apply(scorer):
    """
    У нечисловых факторов на листе нормализации одна строка-«оптимум».
    Подставлять её при K = 0 нельзя: получилось бы «Идеальная пара для
    севооборота» у заведомо плохого предшественника.
    """
    good = {v.factor_id: v for v in scorer.score("A-01", PEA).values}
    assert good["predecessor_good"].k == 1.0
    assert good["predecessor_good"].zone == "Идеальная пара для севооборота"

    poor = {v.factor_id: v for v in scorer.score("A-01", WHEAT).values}
    assert poor["predecessor_good"].k == pytest.approx(0.2)
    assert poor["predecessor_good"].zone == ""


# --- множитель последействия СЗР ---------------------------------------------

def test_residue_multiplier_is_neutral_without_data(scorer):
    """Без агрохимии и метео множитель равен 1.0 — интервалы справочника как есть."""
    # tillage_type — модификатор из категории «Агротехника», в множитель не входит
    assert scorer.residue_multiplier("A-01") == 1.0
    assert scorer.residue_multiplier("A-04") == 1.0


def test_residue_multiplier_extends_interval_on_bad_soil(
    df_sev, compat_matrix, registry, taxonomy, tmp_path
):
    write_soil(tmp_path, {
        "A-01": {"ph": 6.5, "cec": 35, "humus_percent": 5.0},   # оптимум
        "A-02": {"ph": 8.6, "cec": 8, "humus_percent": 1.0},    # солонец, песок
    })
    scorer = FactorScorer(
        planning_year=2027, df_sev=df_sev, compat_matrix=compat_matrix,
        registry=registry, taxonomy=taxonomy, inputs=load_inputs(tmp_path),
    )
    good = scorer.residue_multiplier("A-01")
    bad = scorer.residue_multiplier("A-02")
    assert good < 1.0 < bad
    assert bad <= 2.0
    assert good >= 0.8


def test_residue_multiplier_changes_chemical_ban(df_sev):
    """Растянутый интервал держит культуру заблокированной дольше."""
    from app.core.chemical import (build_field_chemical_history,
                                   get_forbidden_cultures_by_chemicals)
    history = build_field_chemical_history(df_sev)
    intervals = {("имазетапир", SUNFLOWER): 1.0}

    # прошёл 1 год, интервал 1.0 — запрета нет
    assert get_forbidden_cultures_by_chemicals(
        "A-01", 2027, history, intervals, {SUNFLOWER: SUNFLOWER}
    ) == {}
    # тот же интервал, растянутый плохой почвой, — запрет появляется
    forbidden = get_forbidden_cultures_by_chemicals(
        "A-01", 2027, history, intervals, {SUNFLOWER: SUNFLOWER},
        interval_multiplier=1.5,
    )
    assert SUNFLOWER in forbidden
    assert "× 1.50" in forbidden[SUNFLOWER]


# --- жёсткие блокировки и покрытие --------------------------------------------

def test_blocked_cultures_uses_matrix_zero(scorer):
    """A-03 в 2026 — горох; горох по гороху запрещён (значение матрицы 0)."""
    blocked = blocked_cultures(scorer, "A-03", [WHEAT, PEA, SUNFLOWER, MAIZE])
    assert set(blocked) == {PEA}
    assert "Недопустимый предшественник" in blocked[PEA]


def test_coverage_report_counts_only_available_weight(df_sev, compat_matrix):
    report = coverage_report(df_sev, compat_matrix, planning_year=2027)
    assert report["total_weight"] == pytest.approx(100.0)
    assert report["scoring_weight"] == pytest.approx(91.0)
    assert report["constraint_weight"] == pytest.approx(9.0)
    statuses = {f["id"]: f["status"] for f in report["factors"]}
    assert statuses["predecessor_good"] == "available"
    assert statuses["predecessor_value"] == "available"
    assert statuses["tillage_type"] == "available"
    assert statuses["soil_ph"] == "no_data"
    assert statuses["machinery_fleet"] == "not_implemented"
    assert statuses["rotation_history"] == "implemented"
    # 8 (хороший предшественник) + 4 (ценность) + 2 (обработка почвы)
    assert report["available_weight"] == pytest.approx(14.0)
    assert report["coverage"] == pytest.approx(14.0 / 91.0, abs=1e-4)


def test_coverage_grows_with_new_source(df_sev, compat_matrix, tmp_path, monkeypatch):
    write_soil(tmp_path, {f: {"ph": 6.5} for f in build_df_sev()["field_id"].unique()})
    import app.core.factors.scoring as scoring

    original = scoring.get_inputs
    monkeypatch.setattr(scoring, "get_inputs", lambda: load_inputs(tmp_path))
    try:
        report = coverage_report(df_sev, compat_matrix, planning_year=2027)
    finally:
        monkeypatch.setattr(scoring, "get_inputs", original)
    # к прежним 14 баллам добавился pH (8)
    assert report["available_weight"] == pytest.approx(22.0)
    assert "field_soil.csv" in report["sources_present"]
