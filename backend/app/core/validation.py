"""
Валидация оптимизированного решения.
"""
import pandas as pd

from app.core.helpers import is_perennial
from app.core.chemical import (
    build_field_chemical_history,
    get_forbidden_cultures_by_chemicals,
    build_culture_mapping_for_intervals,
)


def validate_solution(
    df_optimized: pd.DataFrame,
    df_sev: pd.DataFrame,
    return_rules: dict,
    compat_matrix: dict,
    safe_intervals: dict,
    crop_targets: dict,
    planning_year: int = 2026,
    hard_ratio: float = 0.90,
) -> tuple[bool, list[dict]]:
    """Проверка оптимизированного решения. Возвращает (is_valid, errors)."""
    errors = []

    # Валидируем только поля из плана — историю и химию строим ЛИШЬ по ним.
    # Раньше field_history строился по ВСЕМ полям df_sev маской-на-поле
    # (df_sev[df_sev['field_id'] == fid] в цикле) — это O(полей × строк) и на
    # полном наборе давало десятки-сотни секунд на каждый расчёт.
    plan_field_ids = set(df_optimized['field_id'].dropna().unique())
    df_plan_hist = df_sev[df_sev['field_id'].isin(plan_field_ids)]

    chem_history = build_field_chemical_history(df_plan_hist)
    field_history: dict = {}
    for field_id, group in df_plan_hist.dropna(subset=['field_id']).groupby('field_id'):
        field_history[field_id] = dict(zip(group['year'], group['culture_unified']))

    all_cultures = df_sev['culture_unified'].dropna().unique()
    opt_cultures = [c for c in all_cultures if not is_perennial(c)]
    culture_mapping_intervals = build_culture_mapping_for_intervals(safe_intervals, opt_cultures)

    # 1. Площади
    plan_areas = df_optimized.groupby('culture_unified')['area_ha'].sum().to_dict()
    for culture, target_area in crop_targets.items():
        min_area = target_area * hard_ratio
        actual_area = plan_areas.get(culture, 0)
        percent = (actual_area / target_area * 100) if target_area > 0 else 0
        if actual_area < min_area:
            errors.append({
                'type': 'Недостаточная площадь (жёсткое)',
                'culture': culture,
                'target': target_area,
                'actual': actual_area,
                'min_required': min_area,
                'percent': percent
            })

    # 2. Химия
    for _, row in df_optimized.iterrows():
        field_id = row['field_id']
        culture = row['culture_unified']
        forbidden = get_forbidden_cultures_by_chemicals(
            field_id, planning_year, chem_history,
            safe_intervals, culture_mapping_intervals
        )
        if culture in forbidden:
            errors.append({
                'type': 'Нарушение хим. интервала',
                'field_id': field_id,
                'culture': culture,
                'reason': forbidden[culture]
            })

    # 3. Возврат и повтор подряд — те же правила, что штрафует солвер
    for _, row in df_optimized.iterrows():
        field_id = row['field_id']
        culture = row['culture_unified']
        if is_perennial(culture):
            continue

        rules = return_rules.get(culture, {'years_to_return': 4, 'max_consecutive': 1})
        years_required = rules['years_to_return']
        max_consec = rules['max_consecutive']
        history = field_history.get(field_id, {})

        for hist_year in range(int(planning_year) - years_required + 1, int(planning_year)):
            if history.get(hist_year) == culture:
                years_gap = planning_year - hist_year
                if 1 < years_gap < years_required:
                    errors.append({
                        'type': 'Ранний возврат',
                        'field_id': field_id,
                        'culture': culture,
                        'years_gap': years_gap,
                        'required': years_required
                    })
                    break

        consecutive = 0
        for hist_year in range(int(planning_year) - 1, int(planning_year) - max_consec - 2, -1):
            if history.get(hist_year) != culture:
                break
            consecutive += 1
        if consecutive >= max_consec:
            errors.append({
                'type': 'Повтор культуры подряд',
                'field_id': field_id,
                'culture': culture,
                'consecutive_years': consecutive + 1,
                'max_consecutive': max_consec,
            })

    # 4. Предшественники
    prev_year = planning_year - 1
    for _, row in df_optimized.iterrows():
        field_id = row['field_id']
        culture = row['culture_unified']
        if is_perennial(culture):
            continue

        pred_culture = field_history.get(field_id, {}).get(prev_year, None)
        if pred_culture and pred_culture != culture and not is_perennial(pred_culture):
            if culture in compat_matrix and pred_culture in compat_matrix.get(culture, {}):
                if compat_matrix[culture][pred_culture] == 0:
                    errors.append({
                        'type': 'Неподходящий предшественник',
                        'field_id': field_id,
                        'culture': culture,
                        'predecessor': pred_culture
                    })

    return len(errors) == 0, errors
