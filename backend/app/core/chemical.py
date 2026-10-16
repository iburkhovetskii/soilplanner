"""
Химическая история и запрещённые культуры.
"""
import pandas as pd
from collections import defaultdict


def build_field_chemical_history(df_sev: pd.DataFrame) -> dict:
    """
    Построить историю химобработки по полям.
    Возвращает: {field_id: {year: [список_веществ_lower]}}
    """
    history = defaultdict(lambda: defaultdict(list))

    if 'active_substance' not in df_sev.columns:
        return history

    # zip по колонкам вместо iterrows() — в разы быстрее на больших df.
    for field_id, year, substances in zip(
        df_sev['field_id'], df_sev['year'], df_sev['active_substance']
    ):
        if pd.isna(field_id) or pd.isna(year) or pd.isna(substances):
            continue

        bucket = history[field_id][int(year)]
        for s in str(substances).split(';'):
            s_clean = s.strip().lower()
            if s_clean:
                bucket.append(s_clean)

    return history


def get_forbidden_cultures_by_chemicals(
    field_id,
    target_year: int,
    chem_history: dict,
    safe_intervals: dict,
    culture_mapping_for_intervals: dict | None = None,
    interval_multiplier: float = 1.0,
) -> dict[str, str]:
    """
    Определить запрещённые культуры для поля в целевом году.

    interval_multiplier — множитель безопасного интервала для этого поля.
    По спецификации факторов СПП скорость распада СЗР зависит от почвы и
    погоды: щелочная реакция, высокая ЕКО, засуха, холод и No-Till растягивают
    последействие (множитель > 1), навоз и тёплый влажный сезон его сокращают
    (< 1). 1.0 — данных нет, справочный интервал берётся как есть
    (см. app.core.factors.FactorScorer.residue_multiplier).

    Возвращает: {культура: причина}
    """
    forbidden = {}

    if field_id not in chem_history:
        return forbidden

    culture_map = culture_mapping_for_intervals or {}
    multiplier = float(interval_multiplier or 1.0)

    for year, substances in chem_history[field_id].items():
        years_passed = target_year - year

        for substance in substances:
            for (subst_ref, culture_ref), interval_years in safe_intervals.items():
                if subst_ref == substance:
                    effective = interval_years * multiplier
                    if years_passed < effective:
                        unified_culture = culture_map.get(culture_ref, culture_ref)
                        reason = (
                            f"Вещество '{substance}' применено в {year} г. "
                            f"Интервал {interval_years:.1f} лет"
                            + (f" × {multiplier:.2f} = {effective:.1f} лет"
                               if abs(multiplier - 1.0) > 1e-9 else "")
                            + f", прошло {years_passed} лет."
                        )
                        forbidden[unified_culture] = reason

    return forbidden


def build_culture_mapping_for_intervals(safe_intervals: dict, opt_cultures: list) -> dict:
    """Создать маппинг культур из справочника интервалов к оптимизируемым культурам."""
    interval_cultures = set(culture for (_, culture) in safe_intervals.keys())

    mapping = {}
    for ic in interval_cultures:
        if ic in opt_cultures:
            mapping[ic] = ic
            continue

        ic_lower = ic.lower()
        for oc in opt_cultures:
            oc_lower = oc.lower()
            if ic_lower in oc_lower or oc_lower.startswith(ic_lower):
                mapping[ic] = oc
                break

    return mapping


def analyze_original_chemical_violations(
    df_sev: pd.DataFrame,
    safe_intervals: dict,
    planning_year: int = 2026
) -> list[dict]:
    """Анализ нарушений химических ограничений в исходных данных."""
    from app.core.helpers import is_perennial

    all_cultures = df_sev['culture_unified'].dropna().unique()
    opt_cultures = [c for c in all_cultures if not is_perennial(c)]
    culture_mapping_intervals = build_culture_mapping_for_intervals(safe_intervals, opt_cultures)

    df_year = df_sev[df_sev['year'] == planning_year]

    # Химию проверяем только для полей этого года — историю строим лишь по ним
    # (по всем годам этих полей), а не по всему df_sev.
    relevant_fields = set(df_year['field_id'].dropna().unique())
    chem_history = build_field_chemical_history(
        df_sev[df_sev['field_id'].isin(relevant_fields)]
    )

    violations = []

    for _, row in df_year.iterrows():
        field_id = row['field_id']
        culture = row['culture_unified']
        area = row['area_ha']

        if pd.isna(culture) or is_perennial(culture):
            continue

        forbidden = get_forbidden_cultures_by_chemicals(
            field_id, planning_year, chem_history,
            safe_intervals, culture_mapping_intervals
        )

        if culture in forbidden:
            violations.append({
                'field_id': field_id,
                'culture': culture,
                'area_ha': float(area),
                'reason': forbidden[culture]
            })

    return violations
