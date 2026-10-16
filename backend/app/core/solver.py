"""
CP-SAT солвер для оптимизации севооборота.
"""
import logging
import pandas as pd
import time
from ortools.sat.python import cp_model

logger = logging.getLogger("soil_planner.solver")

# Доля бюджета времени, отдаваемая первому этапу (севооборот + площади).
# Второй этап стартует с готового решения и только улучшает балл, поэтому
# ему хватает меньшей части бюджета.
TWO_STAGE_SPLIT = 0.7

from app.core.constants import (
    WEIGHT_ROTATION_ERROR,
    WEIGHT_AREA_DEFICIT,
    WEIGHT_COMPACTNESS,
    WEIGHT_FACTOR_SCORE,
    FACTOR_AREA_REF_HA,
    FACTOR_AREA_CAP,
    HARD_BLOCK_BAD_PREDECESSOR,
)
from app.core.helpers import is_perennial, normalize_crop_targets
from app.core.chemical import (
    build_field_chemical_history,
    get_forbidden_cultures_by_chemicals,
    build_culture_mapping_for_intervals,
)


class SolverProgressCallback(cp_model.CpSolverSolutionCallback):
    """Callback для отслеживания прогресса солвера."""

    def __init__(self, progress_fn=None):
        super().__init__()
        self._start_time = time.time()
        self._solutions = 0
        self._progress_fn = progress_fn

    def on_solution_callback(self):
        self._solutions += 1
        elapsed = time.time() - self._start_time
        obj = self.ObjectiveValue()
        if self._progress_fn:
            self._progress_fn({
                'type': 'progress',
                'solutions': self._solutions,
                'objective': int(obj),
                'elapsed': round(elapsed, 1),
            })


def _infeasible_reason(
    hard_min_by_farm: dict,
    available_int_ha: dict,
    hard_ratio: float,
    chem_constraints: int,
    hard_blocked_pairs: int,
) -> str:
    """Короткое объяснение INFEASIBLE для интерфейса."""
    overflows = []
    for farm_key, need in hard_min_by_farm.items():
        have = int(available_int_ha.get(farm_key, 0))
        if need > have:
            overflows.append((farm_key, int(need), have))
    if overflows:
        parts = []
        for farm_key, need, have in overflows:
            prefix = '' if farm_key in (None, '_global') else f'«{farm_key}»: '
            parts.append(
                f'{prefix}жёсткий минимум {hard_ratio:.0%} требует {need} га, '
                f'а поля дают {have} га'
            )
        return (
            'Цели не помещаются в площадь хозяйства. '
            + '; '.join(parts)
            + '. Уменьшите площади культур или снизьте жёсткий минимум.'
        )
    if chem_constraints or hard_blocked_pairs:
        return (
            'Жёсткие запреты по химии или предшественнику не оставляют '
            'допустимого назначения культур.'
        )
    return 'При текущих ограничениях допустимого плана нет.'


def optimize_rotation_with_chemicals(
    df_sev: pd.DataFrame,
    return_rules: dict,
    compat_matrix: dict,
    safe_intervals: dict,
    planning_year: int = 2026,
    crop_targets: dict | None = None,
    hard_ratio: float = 0.90,
    soft_ratio: float = 1.00,
    progress_fn=None,
    max_time: int = 120,
    num_workers: int = 8,
    weight_compactness: int = WEIGHT_COMPACTNESS,
    weight_adjacency: int = 0,
    adjacency_radius_m: float = 1200.0,
    field_overrides: dict | None = None,
    factor_scorer=None,
    weight_factor_score: int = WEIGHT_FACTOR_SCORE,
    factor_area_weighted: bool = False,
    hard_block_bad_predecessor: bool = HARD_BLOCK_BAD_PREDECESSOR,
    allowed_cultures: set | None = None,
) -> tuple[pd.DataFrame | None, dict]:
    """
    Оптимизация севооборота с учетом химобработки.

    Args:
        progress_fn: callable для отправки прогресса (SSE)
        max_time: максимальное время решения в секундах
        num_workers: количество потоков солвера
        weight_compactness: вес мягкого ограничения «административной кучности»
            (штраф за каждую различную культуру внутри подразделения). 0 — выкл.
        weight_adjacency: вес мягкого ограничения «гео-кучности» — штраф за
            каждую пару соседних полей с разными культурами (соседство = центроиды
            в радиусе adjacency_radius_m). 0 — выкл. Рекомендуется 3..10.
        adjacency_radius_m: радиус соседства для гео-кучности (метры).
        field_overrides: {field_id: нецелевая_категория} — поля, вручную
            исключённые из планирования. Не участвуют в солвере (нет переменных,
            не учитываются в площадях/кучности), но попадают в итоговый план с
            указанной категорией (например, «Пар», «Строительство»).
        factor_scorer: app.core.factors.FactorScorer — балльная оценка пар
            «поле → культура» по факторам формирования СПП. None — фактор-балл
            в целевую функцию не входит (поведение до внедрения модели).
        weight_factor_score: вес «сожаления» по баллу факторов. 0 — выкл.
        factor_area_weighted: домножать коэффициент на площадь поля
            (min(area / FACTOR_AREA_REF_HA, FACTOR_AREA_CAP)). По умолчанию
            выкл: без ограничителя крупные поля ломают иерархию весов.
        hard_block_bad_predecessor: жёстко запрещать культуру после
            недопустимого предшественника (значение матрицы совместимости 0)
            вместо мягкого штрафа. Может привести к INFEASIBLE — см.
            docs/FACTORS_SPP.md.
        allowed_cultures: набор культур, которые вообще можно назначать. Без
            него в кандидаты попадают все написания из истории, включая
            составные («Пар;Чечевица», «Кукуруза силос; Кукуруза зерно») —
            их солвер имеет право поставить на поле, не связанное целями.
            Целевые культуры и нецелевые категории проходят всегда.
    """
    from app.core.helpers import extract_crop_targets

    model = cp_model.CpModel()

    # План строим только по «актуальным» полям — тем, что есть в последнем
    # доступном году ≤ planning_year (это тот же снимок, из которого берутся
    # целевые площади). Иначе в план попадали ВСЕ поля за всю историю хозяйства,
    # включая давно не существующие (старая нарезка/нумерация), что раздувало
    # площади в разы: напр. «Защитное Север» — 902 поля / 71 734 га по всей
    # истории против ~15 полей / 1 702 га в актуальном 2026 году.
    all_field_ids = df_sev['field_id'].dropna().unique()
    _years_le = [int(y) for y in df_sev['year'].dropna().unique() if int(y) <= planning_year]
    snapshot_year = max(_years_le) if _years_le else None
    if snapshot_year is not None:
        _snapshot_fields = set(
            df_sev[df_sev['year'] == snapshot_year]['field_id'].dropna().unique()
        )
        fields = [f for f in all_field_ids if f in _snapshot_fields]
    else:
        fields = list(all_field_ids)

    all_cultures = df_sev['culture_unified'].dropna().unique()
    opt_cultures = [c for c in all_cultures if not is_perennial(c)]

    # Цели могут содержать культуры, которых НЕТ в истории этого подмножества
    # (типичный случай: пользователь добавил в план культуру, ранее не
    # выращивавшуюся в этом хозяйстве). Раньше такие культуры молча выпадали:
    # для них не создавались ни переменные x, ни ограничение по площади, поэтому
    # солвер их никогда не назначал. Расширяем набор оптимизируемых культур
    # целевыми (кроме многолетних), чтобы их можно было высадить.
    if crop_targets is None:
        crop_targets = extract_crop_targets(df_sev, planning_year)
    _targets_nested, _ = normalize_crop_targets(crop_targets)

    # Отсекаем «мусорные» написания из кандидатов. В истории встречаются
    # составные значения («Пар;Чечевица», «Кукуруза силос; Кукуруза зерно»):
    # солвер имел право поставить их на любое поле, не связанное целями, —
    # площади они не нарушали, а в плане выглядели как реальная культура.
    if allowed_cultures:
        _target_cultures = {
            c for t in _targets_nested.values() for c, ha in t.items() if ha and ha > 0
        }
        _keep = set(allowed_cultures) | _target_cultures
        _dropped = [c for c in opt_cultures if c not in _keep]
        opt_cultures = [c for c in opt_cultures if c in _keep]
        if _dropped:
            logger.info(
                'Из кандидатов исключено %s названий вне справочника культур '
                '(например: %s)', len(_dropped), ', '.join(map(str, _dropped[:3])),
            )

    _existing = set(opt_cultures)
    for _farm_targets in _targets_nested.values():
        for _culture, _ha in _farm_targets.items():
            if _ha and _ha > 0 and _culture not in _existing and not is_perennial(_culture):
                opt_cultures.append(_culture)
                _existing.add(_culture)

    culture_mapping_intervals = build_culture_mapping_for_intervals(safe_intervals, opt_cultures)
    culture_to_idx = {c: i for i, c in enumerate(opt_cultures)}

    # Данные по полям (groupby вместо поэлементной фильтрации — O(n) вместо O(n*m))
    field_areas = {}
    field_history = {}
    field_subdivision = {}
    field_farm: dict = {}

    has_subdivision = 'subdivision' in df_sev.columns
    has_farm = 'farm' in df_sev.columns

    for field_id, group in df_sev.dropna(subset=['field_id']).groupby('field_id'):
        field_areas[field_id] = group['area_ha'].iloc[0]
        field_history[field_id] = dict(zip(group['year'], group['culture_unified']))
        if has_subdivision:
            sub = group['subdivision'].dropna()
            field_subdivision[field_id] = sub.iloc[0] if len(sub) else None
        if has_farm:
            farms = group['farm'].dropna()
            field_farm[field_id] = farms.iloc[0] if len(farms) else None

    # Площадь поля берём из снимка snapshot_year (актуальная нарезка), а не из
    # произвольной строки истории.
    if snapshot_year is not None:
        _snap = (df_sev[df_sev['year'] == snapshot_year]
                 .dropna(subset=['field_id']).drop_duplicates('field_id'))
        for _fid, _area in zip(_snap['field_id'], _snap['area_ha']):
            if pd.notna(_area):
                field_areas[_fid] = _area

    chem_history = build_field_chemical_history(df_sev)

    # Оптимизируемые поля.
    # «Текущая культура» = запись поля в planning_year; если её нет (типичный
    # случай для будущего года), берём fallback на ПОСЛЕДНИЙ доступный год
    # ≤ planning_year. Это позволяет планировать «вперёд» (например, 2027 на
    # основе данных по 2026) без необходимости засевать фейковые строки в df.
    # Поле optimizable, если текущая культура не многолетка.
    optimizable_fields = set()
    for field_id in fields:
        cult = field_history[field_id].get(planning_year)
        if cult is None:
            prev_years = [y for y in field_history[field_id].keys()
                          if y is not None and y < planning_year]
            if not prev_years:
                continue  # поле никогда не существовало — пропускаем
            cult = field_history[field_id][max(prev_years)]
        if not is_perennial(cult):
            optimizable_fields.add(field_id)

    # Поля, вручную исключённые пользователем из планирования. Их убираем из
    # оптимизируемых (солвер их не трогает), а в итоговый план допишем ниже с
    # выбранной нецелевой категорией.
    override_map = {str(k): v for k, v in (field_overrides or {}).items() if v}
    override_fields = {fid for fid in fields if str(fid) in override_map}
    optimizable_fields -= override_fields

    logger.info(
        "Построение модели: год=%s, полей_всего=%s, оптимизируемых=%s, культур=%s, "
        "целей_ферм=%s, max_time=%sс, workers=%s, кучность=%s, смежность=%s",
        planning_year, len(fields), len(optimizable_fields), len(opt_cultures),
        len(_targets_nested), max_time, num_workers, weight_compactness, weight_adjacency,
    )
    if progress_fn:
        progress_fn({
            'type': 'status',
            'message': f'Построение модели: {len(optimizable_fields)} полей, {len(opt_cultures)} культур',
        })

    # === ПЕРЕМЕННЫЕ РЕШЕНИЯ ===
    x = {}
    for field_id in optimizable_fields:
        for culture in opt_cultures:
            x[field_id, culture] = model.NewBoolVar(f'x_{field_id}_{culture}')

    error_vars = []

    # === БАЛЛ ФАКТОРОВ СПП ===
    # Считаем матрицу баллов ОДИН раз: на крупное хозяйство это ~32 тыс. пар,
    # и пересчёт внутри циклов ниже стоил бы дороже самого решения.
    prev_year_for_pred = planning_year - 1
    factor_scores: dict = {}
    if factor_scorer is not None:
        for field_id in optimizable_fields:
            for culture in opt_cultures:
                factor_scores[field_id, culture] = factor_scorer.score_int(
                    field_id, culture
                )

    # Химические запреты считаем здесь, до жёсткого запрета предшественника:
    # предохранитель ниже должен видеть ОБА набора запретов, иначе поле, где
    # химия и предшественник вместе закрывают все культуры, сделает модель
    # нерешаемой. Сами ограничения ставятся в блоке 3.
    chem_forbidden: dict = {}
    for field_id in optimizable_fields:
        forbidden = get_forbidden_cultures_by_chemicals(
            field_id, planning_year, chem_history,
            safe_intervals, culture_mapping_intervals,
            interval_multiplier=(factor_scorer.residue_multiplier(field_id)
                                 if factor_scorer is not None else 1.0),
        )
        if forbidden:
            chem_forbidden[field_id] = forbidden

    # Жёсткий запрет недопустимого предшественника (спецификация факторов:
    # «Алгоритм полностью исключает культуру»). Предохранитель: если запрет
    # вместе с химическим не оставляет полю ни одной культуры, для этого поля
    # он не применяется — иначе модель гарантированно INFEASIBLE.
    hard_blocked_pairs: set = set()
    if hard_block_bad_predecessor and compat_matrix:
        all_cultures = set(opt_cultures)
        for field_id in optimizable_fields:
            pred_culture = field_history[field_id].get(prev_year_for_pred)
            if not pred_culture or is_perennial(pred_culture):
                continue
            # Диагональ матрицы включена намеренно: «горох по гороху» = 0 —
            # это тот же недопустимый предшественник. Мягкий штраф ниже её
            # пропускает (монокультуру там ловит max_consecutive), поэтому
            # жёсткий режим строго строже мягкого.
            blocked_here = {
                culture for culture in opt_cultures
                if compat_matrix.get(culture, {}).get(pred_culture) == 0
            }
            if not blocked_here:
                continue
            chem_here = {c for c in chem_forbidden.get(field_id, {})
                         if c in culture_to_idx}
            if not (all_cultures - blocked_here - chem_here):
                logger.warning(
                    "Поле %s: жёсткий запрет предшественника вместе с химическим "
                    "не оставляет ни одной культуры из %s — запрет не применён, "
                    "остаётся мягкий штраф",
                    field_id, len(opt_cultures),
                )
                continue
            for culture in blocked_here:
                model.Add(x[field_id, culture] == 0)
                hard_blocked_pairs.add((field_id, culture))

    # 1. Каждое поле — ровно одна культура
    for field_id in optimizable_fields:
        model.Add(sum(x[field_id, c] for c in opt_cultures) == 1)

    # 2. Ограничения по площадям — поддерживаем оба формата crop_targets:
    #    flat {culture: ha} (глобально) и nested {farm: {culture: ha}} (per-farm).
    targets_nested, is_per_farm = normalize_crop_targets(crop_targets)
    deficit_vars = {}  # ключ: (farm, culture) или (None, culture) для flat
    max_deficit_percent = int((soft_ratio - hard_ratio) * 100)

    # Разбиваем поля по хозяйствам (для nested-режима);
    # в flat-режиме всё сваливаем под фиктивный ключ '_global'.
    if is_per_farm:
        fields_by_farm: dict[str, list] = {}
        for field_id in optimizable_fields:
            farm = field_farm.get(field_id)
            if farm is None:
                continue
            fields_by_farm.setdefault(farm, []).append(field_id)
    else:
        fields_by_farm = {'_global': list(optimizable_fields)}

    available_int_ha = {
        farm_key: sum(int(field_areas.get(fid, 0) or 0) for fid in fids)
        for farm_key, fids in fields_by_farm.items()
    }
    hard_min_by_farm: dict[str, int] = {}

    for farm_key, farm_targets in targets_nested.items():
        farm_fields = fields_by_farm.get(farm_key, [])
        if not farm_fields:
            continue
        for culture, target_area in farm_targets.items():
            if target_area <= 0 or culture not in culture_to_idx:
                continue
            label = f'{farm_key}_{culture}' if is_per_farm else culture
            deficit_var = model.NewIntVar(
                0, max_deficit_percent, f'deficit_{label}'
            )
            deficit_vars[(farm_key, culture)] = deficit_var

            total_area = sum(
                x[field_id, culture] * int(field_areas.get(field_id, 0))
                for field_id in farm_fields
            )

            hard_min = int(target_area * hard_ratio)
            hard_min_by_farm[farm_key] = hard_min_by_farm.get(farm_key, 0) + hard_min
            model.Add(total_area >= hard_min)

            soft_target = int(target_area * soft_ratio)
            model.Add(
                total_area * 100 + deficit_var * int(target_area)
                >= soft_target * 100
            )

    # 3. Химические ограничения (запреты посчитаны выше, до предохранителя)
    chem_constraints_count = 0
    for field_id, forbidden in chem_forbidden.items():
        for culture in forbidden:
            if culture in culture_to_idx:
                model.Add(x[field_id, culture] == 0)
                chem_constraints_count += 1

    # 4. Штрафы за нарушения севооборота
    prev_year = planning_year - 1

    for field_id in optimizable_fields:
        for culture in opt_cultures:
            rules = return_rules.get(culture, {'years_to_return': 4, 'max_consecutive': 1})
            years_required = rules['years_to_return']
            max_consec = rules['max_consecutive']

            # Ранний возврат
            for hist_year in range(int(planning_year) - years_required + 1, int(planning_year)):
                hist_culture = field_history[field_id].get(hist_year, None)
                if hist_culture == culture:
                    years_gap = planning_year - hist_year
                    if 1 < years_gap < years_required:
                        error_var = model.NewBoolVar(
                            f'err_return_{field_id}_{culture}_{hist_year}'
                        )
                        model.Add(error_var >= x[field_id, culture])
                        error_vars.append(error_var)

            # Подряд (max_consecutive)
            consecutive_in_history = 0
            for hist_y in range(int(planning_year) - 1, int(planning_year) - max_consec - 2, -1):
                hist_culture = field_history[field_id].get(hist_y, None)
                if hist_culture == culture:
                    consecutive_in_history += 1
                else:
                    break

            if consecutive_in_history >= max_consec:
                error_var = model.NewBoolVar(f'err_consec_{field_id}_{culture}')
                model.Add(error_var >= x[field_id, culture])
                error_vars.append(error_var)

            # Неподходящий предшественник. Если пара уже запрещена жёстко,
            # мягкий штраф не добавляем — иначе один и тот же сигнал учтётся
            # дважды (переменная всё равно зафиксирована в 0).
            pred_culture = field_history[field_id].get(prev_year, None)
            if pred_culture and pred_culture != culture and not is_perennial(pred_culture):
                if culture in compat_matrix and pred_culture in compat_matrix.get(culture, {}):
                    if (compat_matrix[culture][pred_culture] == 0
                            and (field_id, culture) not in hard_blocked_pairs):
                        error_var = model.NewBoolVar(
                            f'err_pred_{field_id}_{culture}_{pred_culture}'
                        )
                        model.Add(error_var >= x[field_id, culture])
                        error_vars.append(error_var)

    # 5. Кучность: одинаковые культуры — рядом (внутри подразделения).
    # Для каждой пары (подразделение, культура) булевская z[s,c] = 1, если
    # эта культура присутствует хотя бы на одном поле подразделения.
    # Минимизируя сумму z, поощряем меньшее число различных культур
    # на подразделение → больше «кучности».
    compactness_vars = {}
    if weight_compactness > 0 and has_subdivision:
        # Группируем оптимизируемые поля по подразделению
        sub_to_fields = {}
        for field_id in optimizable_fields:
            sub = field_subdivision.get(field_id)
            if sub is None:
                continue
            sub_to_fields.setdefault(sub, []).append(field_id)

        for sub, fields_in_sub in sub_to_fields.items():
            if len(fields_in_sub) < 2:
                continue  # одиночное поле — кучность не применима
            for culture in opt_cultures:
                z = model.NewBoolVar(f'z_{sub}_{culture}')
                # z >= x[f, c] для каждого поля → если хоть одно поле имеет c, z=1
                for field_id in fields_in_sub:
                    model.Add(z >= x[field_id, culture])
                compactness_vars[(sub, culture)] = z

    # 6. Гео-кучность (подход 1): штраф за «разноцветные» границы соседних полей.
    # Соседи определяются графом смежности по центроидам в радиусе R.
    # Для каждого ребра (i, j) — bool diff_ij = 1, если культуры разные.
    # Линеаризация без same-переменных:
    #     diff_ij >= x[i, c] - x[j, c]
    #     diff_ij >= x[j, c] - x[i, c]   для каждой культуры c.
    # Поскольку каждое поле получает ровно одну культуру, diff_ij = 1 iff
    # culture(i) != culture(j).
    adj_diff_vars: dict = {}
    adj_edges: list = []
    if weight_adjacency > 0:
        from app.core.data_loader import load_field_geometries
        from app.core.adjacency_graph import build_adjacency_graph
        geoms = load_field_geometries()
        adj_edges = build_adjacency_graph(
            geoms,
            fields=list(optimizable_fields),
            radius_m=adjacency_radius_m,
        )
        for i, j, _w in adj_edges:
            diff = model.NewBoolVar(f'adj_diff_{i}_{j}')
            for c in opt_cultures:
                model.Add(diff >= x[i, c] - x[j, c])
                model.Add(diff >= x[j, c] - x[i, c])
            adj_diff_vars[(i, j)] = diff

    # 7. Балл факторов СПП — в цель входит «сожаление»: насколько выбранная
    # культура хуже лучшей возможной на этом поле. Так слагаемое равно нулю на
    # идеальном плане и не сдвигает шкалу objective_value, а оптимум совпадает
    # с оптимумом по «сырому» баллу (разница — константа на поле).
    # «Сожаление» считаем всегда, когда есть баллы: это ещё и показатель отчёта
    # («насколько выбранная культура хуже лучшей возможной на поле»).
    # В цель оно попадает только при ненулевом весе.
    # Авторитет балла: доля решающих факторов, реально обеспеченных данными.
    # Из 100 баллов спецификации выбор культуры определяют 36 — остальные
    # описывают поле и в задаче назначения дают константу. Пока обеспечена
    # только часть этих 36, балл распоряжается планом ровно в той же
    # пропорции: иначе фактор весом 4 действовал бы как фактор весом 12.
    factor_authority = 1.0
    if factor_scorer is not None and hasattr(factor_scorer, 'authority'):
        try:
            factor_authority = float(
                factor_scorer.authority(list(optimizable_fields))
            )
        except Exception:
            logger.exception('Не удалось оценить авторитет балла факторов')
            factor_authority = 1.0

    factor_terms: list = []
    factor_coefficients: dict = {}
    factor_regrets: dict = {}
    # Балл сравнивает культуры ВНУТРИ производственной программы. Культуре вне
    # целей присваивается худший балл поля, поэтому балл никогда не «вытягивает»
    # поле из программы: без этого он охотно менял целевую культуру на
    # непрограммную с формально высоким баллом, оплачивая недобор площадей
    # (штраф за недобор по построению слабый — не более 10 единиц на культуру).
    _programme = {
        c for t in _targets_nested.values() for c, ha in t.items() if ha and ha > 0
    }
    if factor_scores:
        for field_id in optimizable_fields:
            allowed = [c for c in opt_cultures
                       if (field_id, c) not in hard_blocked_pairs]
            if not allowed:
                continue
            pool = [c for c in allowed if c in _programme] or allowed
            best = max(factor_scores[field_id, c] for c in pool)
            worst = min(factor_scores[field_id, c] for c in pool)
            area_factor = 1.0
            if factor_area_weighted:
                area = float(field_areas.get(field_id, 0) or 0)
                area_factor = min(area / FACTOR_AREA_REF_HA, FACTOR_AREA_CAP)
            for culture in allowed:
                score_here = (factor_scores[field_id, culture]
                              if culture in pool else worst)
                regret = best - score_here
                factor_regrets[field_id, culture] = regret
                if weight_factor_score <= 0:
                    continue
                coefficient = int(round(
                    weight_factor_score * factor_authority * regret * area_factor
                ))
                if coefficient <= 0:
                    continue
                factor_coefficients[field_id, culture] = coefficient
                factor_terms.append(coefficient * x[field_id, culture])

    # === ЦЕЛЕВАЯ ФУНКЦИЯ ===
    rotation_penalty = WEIGHT_ROTATION_ERROR * sum(error_vars)
    deficit_penalty = WEIGHT_AREA_DEFICIT * sum(deficit_vars.values())
    compactness_penalty = (
        weight_compactness * sum(compactness_vars.values())
        if compactness_vars else 0
    )
    adjacency_penalty = (
        weight_adjacency * sum(adj_diff_vars.values())
        if adj_diff_vars else 0
    )
    factor_penalty = sum(factor_terms) if factor_terms else 0
    base_penalty = (rotation_penalty + deficit_penalty
                    + compactness_penalty + adjacency_penalty)
    two_stage = bool(factor_terms)
    model.Minimize(base_penalty if two_stage
                   else base_penalty + factor_penalty)

    logger.info(
        "Модель построена. Переменных x=%s, ограничений: химия=%s, ошибки_севооборота=%s, "
        "дефицит=%s, кучность=%s, смежность=%s, жёстких_запретов_предшественника=%s, "
        "пар_с_баллом=%s (вес %s). Решаем (max_time=%sс, workers=%s)...",
        len(x), chem_constraints_count, len(error_vars), len(deficit_vars),
        len(compactness_vars), len(adj_diff_vars), len(hard_blocked_pairs),
        len(factor_coefficients), weight_factor_score, max_time, num_workers,
    )
    if progress_fn:
        progress_fn({
            'type': 'status',
            'message': f'Модель построена. Ограничений: химия={chem_constraints_count}, '
                       f'ошибки={len(error_vars)}, дефицит={len(deficit_vars)}, '
                       f'кучность={len(compactness_vars)}. Решаем...',
        })

    # === РЕШЕНИЕ ===
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = max_time
    solver.parameters.num_search_workers = num_workers

    # Детальный лог поиска CP-SAT — пишем в файловый лог (logs/app.log) через
    # logger 'soil_planner.solver', а не в stdout. Это и есть «логи солвера».
    solver.parameters.log_search_progress = True
    solver.parameters.log_to_stdout = False
    solver.log_callback = lambda line: logger.info("[cp-sat] %s", line.rstrip())

    callback = SolverProgressCallback(progress_fn) if progress_fn else None

    # Решаем в два этапа, когда включён балл факторов.
    #
    # Иначе балл и площади оказываются в одной сумме и торгуются друг с другом:
    # штраф за недобор по построению слабый (не более 10 единиц на культуру),
    # поэтому балл при любом положительном весе немедленно скупает весь мягкий
    # допуск площадей — на реальном хозяйстве недобор рос с 59 до 917 га уже
    # при весе 1. Подобрать вес, при котором балл и работает, и не трогает
    # программу, в одной целевой функции невозможно: либо он ничего не решает,
    # либо забирает весь допуск.
    #
    # Этап 1 ищет оптимум по севообороту, площадям и кучности. Этап 2 фиксирует
    # достигнутое значение как ограничение и максимизирует балл только среди
    # равноценных решений — программа площадей не может ухудшиться ни на пункт.
    # Общий бюджет времени не растёт: он делится между этапами.
    stage1_time = max(1.0, max_time * TWO_STAGE_SPLIT) if two_stage else max_time
    solver.parameters.max_time_in_seconds = stage1_time

    start_time = time.time()
    if callback:
        status = solver.Solve(model, callback)
    else:
        status = solver.Solve(model)

    stage2_status = None
    base_objective_stage1 = None
    if two_stage and status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        base_objective_stage1 = int(solver.ObjectiveValue())
        # Подсказываем этапу 2 решение этапа 1: оно заведомо допустимо, поэтому
        # второй этап не может закончиться хуже и не может стать нерешаемым.
        model.ClearHints()
        for (field_id, culture), var in x.items():
            model.AddHint(var, solver.Value(var))
        model.Add(base_penalty <= base_objective_stage1)
        model.Minimize(factor_penalty)

        solver.parameters.max_time_in_seconds = max(
            1.0, max_time - (time.time() - start_time)
        )
        if progress_fn:
            progress_fn({
                'type': 'status',
                'message': f'План по площадям найден (цель {base_objective_stage1}). '
                           f'Улучшаем агрономический балл среди равноценных решений...',
            })
        stage2_status = solver.Solve(model, callback) if callback else solver.Solve(model)
        if stage2_status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            status = stage2_status
        else:
            # Этап 2 не нашёл решения (кончилось время до первой инкумбенты) —
            # такого быть не должно при поданной подсказке, но если случилось,
            # честнее пересобрать модель без балла, чем потерять план.
            logger.warning(
                'Этап 2 (балл факторов) завершился со статусом %s — '
                'возвращаем решение этапа 1',
                solver.StatusName(stage2_status),
            )
            model.Minimize(base_penalty)
            solver.parameters.max_time_in_seconds = max(1.0, max_time * 0.2)
            status = solver.Solve(model, callback) if callback else solver.Solve(model)

    solve_time = time.time() - start_time
    logger.info(
        "Решение завершено: статус=%s, время=%.1fс, этапов=%s, цель_этапа_1=%s",
        solver.StatusName(status), solve_time, 2 if two_stage else 1,
        base_objective_stage1,
    )

    if status in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
        rotation_errors_count = sum(solver.Value(e) for e in error_vars)
        deficit_total_percent = sum(solver.Value(d) for d in deficit_vars.values())
        rotation_cost = rotation_errors_count * WEIGHT_ROTATION_ERROR
        deficit_cost = deficit_total_percent * WEIGHT_AREA_DEFICIT

        # Дефициты по культурам (ключ deficit_vars — (farm_key, culture))
        deficit_info = {}
        for (farm_key, culture), deficit_var in deficit_vars.items():
            deficit_percent = solver.Value(deficit_var)
            if deficit_percent <= 0:
                continue
            target = targets_nested.get(farm_key, {}).get(culture, 0)
            deficit_ha = deficit_percent * target / 100
            entry = {
                'target_ha': target,
                'deficit_percent': deficit_percent,
                'deficit_ha': deficit_ha,
            }
            if is_per_farm:
                entry['farm'] = farm_key
                deficit_info[f'{farm_key} / {culture}'] = entry
            else:
                deficit_info[culture] = entry

        # Собираем план (с farm/subdivision для downstream — карт, сравнений, geojson).
        # factor_score/factor_regret заполняются только для полей, которые
        # реально оптимизировались: у многолетников и ручных исключений балла
        # нет, и None здесь принципиален — иначе средний балл плана поедет вниз.
        def _row(field_id, culture, scored: bool = False):
            row = {
                'field_id': field_id,
                'year': int(planning_year),
                'culture_unified': culture,
                'area_ha': field_areas.get(field_id, 0),
                'farm': field_farm.get(field_id) if has_farm else None,
                'subdivision': field_subdivision.get(field_id) if has_subdivision else None,
                'predecessor': field_history.get(field_id, {}).get(prev_year_for_pred),
                'factor_score': None,
                'factor_regret': None,
            }
            if scored and factor_scores:
                row['factor_score'] = factor_scores.get((field_id, culture))
                row['factor_regret'] = factor_regrets.get((field_id, culture))
            return row

        optimized_plan = []
        for field_id in optimizable_fields:
            for culture in opt_cultures:
                if solver.Value(x[field_id, culture]) == 1:
                    optimized_plan.append(_row(field_id, culture, scored=True))

        # Поля, исключённые из планирования вручную — пишем выбранную нецелевую
        # категорию (солвер их не оптимизировал).
        for field_id in override_fields:
            optimized_plan.append(_row(field_id, override_map[str(field_id)]))

        # Многолетние (не оптимизировались) — копируем культуру из planning_year
        # или, если её нет, из последнего доступного года.
        perennial_fields = set(fields) - optimizable_fields - override_fields
        for field_id in perennial_fields:
            culture = field_history[field_id].get(planning_year)
            if culture is None:
                prev = [y for y in field_history[field_id].keys()
                        if y is not None and y < planning_year]
                if prev:
                    culture = field_history[field_id][max(prev)]
            if culture:
                optimized_plan.append(_row(field_id, culture))

        compactness_distinct = sum(solver.Value(z) for z in compactness_vars.values())
        compactness_cost = compactness_distinct * weight_compactness

        adj_diff_count = sum(solver.Value(d) for d in adj_diff_vars.values())
        adj_total_edges = len(adj_diff_vars)
        adj_cost = adj_diff_count * weight_adjacency

        # Сводка по баллу факторов считается по фактически выбранным парам.
        factor_score_sum = 0.0
        factor_score_area_sum = 0.0
        factor_area_sum = 0.0
        factor_regret_total = 0
        factor_cost = 0
        n_scored = 0
        for row in optimized_plan:
            score = row.get('factor_score')
            if score is None:
                continue
            n_scored += 1
            factor_score_sum += score
            area = float(row.get('area_ha') or 0)
            factor_score_area_sum += score * area
            factor_area_sum += area
            factor_regret_total += row.get('factor_regret') or 0
            factor_cost += factor_coefficients.get(
                (row['field_id'], row['culture_unified']), 0
            )

        # На двух этапах solver.ObjectiveValue() относится только к последнему
        # этапу, поэтому цель собираем из слагаемых — так она сопоставима с
        # прогонами без балла и с сохранёнными результатами.
        total_objective = int(rotation_cost + deficit_cost + compactness_cost
                              + adj_cost + factor_cost)

        result_info = {
            'objective_value': total_objective,
            'two_stage': two_stage,
            'base_objective': base_objective_stage1,
            'factor_weight': weight_factor_score,
            'factor_authority': round(factor_authority, 4),
            'factor_weight_effective': round(weight_factor_score * factor_authority, 3),
            'factor_scored_fields': n_scored,
            'factor_score_avg': round(factor_score_sum / n_scored, 2) if n_scored else None,
            'factor_score_area_avg': (round(factor_score_area_sum / factor_area_sum, 2)
                                      if factor_area_sum > 0 else None),
            'factor_score_sum': round(factor_score_sum, 2),
            'factor_score_area_sum': round(factor_score_area_sum, 2),
            'factor_area_sum': round(factor_area_sum, 2),
            'factor_regret_total': factor_regret_total,
            'factor_cost': factor_cost,
            'hard_blocked_pred_pairs': len(hard_blocked_pairs),
            'hard_block_pred_enabled': bool(hard_block_bad_predecessor),
            'rotation_errors': rotation_errors_count,
            'rotation_cost': rotation_cost,
            'deficit_total_percent': deficit_total_percent,
            'deficit_cost': deficit_cost,
            'deficit_by_culture': deficit_info,
            'compactness_distinct_pairs': int(compactness_distinct),
            'compactness_cost': compactness_cost,
            'compactness_weight': weight_compactness,
            'adjacency_diff_edges': int(adj_diff_count),
            'adjacency_total_edges': adj_total_edges,
            'adjacency_cost': adj_cost,
            'adjacency_weight': weight_adjacency,
            'adjacency_radius_m': adjacency_radius_m,
            'solve_time': round(solve_time, 1),
            'status': solver.StatusName(status),
        }

        return pd.DataFrame(optimized_plan), result_info
    else:
        return None, {
            'status': solver.StatusName(status),
            'solve_time': round(solve_time, 1),
            'hard_block_pred_enabled': bool(hard_block_bad_predecessor),
            'hard_blocked_pred_pairs': len(hard_blocked_pairs),
            'infeasible_reason': _infeasible_reason(
                hard_min_by_farm, available_int_ha, hard_ratio,
                chem_constraints_count, len(hard_blocked_pairs),
            ),
        }
