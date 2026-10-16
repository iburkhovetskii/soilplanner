"""
Optimization endpoint with SSE progress streaming.
"""
import asyncio
import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.auth.router import get_current_user, User
from app.core.data_loader import get_all_data
from app.core.helpers import extract_crop_targets, json_safe
from app.core.chemical import analyze_original_chemical_violations
from app.core.constants import (
    WEIGHT_ROTATION_ERROR,
    WEIGHT_AREA_DEFICIT,
    WEIGHT_COMPACTNESS,
    WEIGHT_FACTOR_SCORE,
    WEIGHT_FACTOR_SCORE_MAX,
    HARD_BLOCK_BAD_PREDECESSOR,
)
from app.config import settings

router = APIRouter(prefix="/api/optimization", tags=["optimization"])

logger = logging.getLogger("soil_planner.runs")

# Cache last result in memory
_last_result: dict | None = None

# Сериализуем расчёты: CP-SAT — CPU-bound, на малой VM (2 ядра) параллельные
# прогоны забивают CPU и память. Один прогон за раз.
_run_lock = asyncio.Lock()


class OptimizationRequest(BaseModel):
    # Поддерживает два формата:
    # - flat:   {culture: ha}              -- ограничения глобально по всему набору
    # - nested: {farm: {culture: ha}}      -- ограничения отдельно по каждому хозяйству
    crop_targets: dict | None = None
    hard_ratio: float = 0.90
    soft_ratio: float = 1.00
    planning_year: int = 2026
    weight_compactness: int = WEIGHT_COMPACTNESS
    weight_adjacency: int = 0  # 0 = выключено; рекомендованное 3-10
    # Балл факторов формирования СПП (docs/FACTORS_SPP.md). Верхняя граница
    # жёсткая: при большем весе балл начинает «покупать» ошибки севооборота.
    weight_factor_score: int = Field(
        WEIGHT_FACTOR_SCORE, ge=0, le=WEIGHT_FACTOR_SCORE_MAX
    )
    # Домножать балл на площадь поля (с ограничителем). По умолчанию выкл.
    factor_area_weighted: bool = False
    # Жёстко запрещать недопустимого предшественника вместо мягкого штрафа.
    hard_block_bad_predecessor: bool = HARD_BLOCK_BAD_PREDECESSOR
    # {field_id: нецелевая_категория} — поля, исключённые из планирования вручную.
    # Не участвуют в солвере, но попадают в итоговый план с этой категорией.
    field_overrides: dict | None = None
    # Идентификатор запуска (генерится фронтом). Под ним результат сохраняется
    # на диск и показывается в списке «недавних».
    run_id: str | None = None


def _run_solver_single_farm(
    df_sev,
    return_rules,
    compat_matrix,
    safe_intervals,
    *,
    farm: str | None,
    flat_targets: dict[str, float],
    planning_year: int,
    hard_ratio: float,
    soft_ratio: float,
    weight_compactness: int,
    weight_adjacency: int,
    field_overrides: dict | None = None,
    weight_factor_score: int = WEIGHT_FACTOR_SCORE,
    factor_area_weighted: bool = False,
    hard_block_bad_predecessor: bool = HARD_BLOCK_BAD_PREDECESSOR,
    progress_fn=None,
) -> tuple[object, dict]:
    """
    Один прогон солвера на подмножестве df_sev (одно хозяйство либо весь набор,
    если farm=None). Принимает flat crop_targets для этого подмножества.
    """
    from app.core.solver import optimize_rotation_with_chemicals
    df_subset = df_sev if farm is None else df_sev[df_sev['farm'] == farm].copy()
    # Солвер берёт area_ha из первой строки field_id через groupby.first().
    # У части строк area_ha = NaN — отсортируем, чтобы непустые шли первыми,
    # иначе int(NaN) внутри солвера сломается.
    df_subset = df_subset.sort_values('area_ha', na_position='last', kind='stable')

    # Назначать можно только культуры из справочника (плюс целевые и нецелевые
    # категории): в истории встречаются составные написания вроде
    # «Пар;Чечевица», которые не должны попадать в план как культура.
    allowed = _allowed_cultures()

    # Балльная оценка факторов строится на подмножестве одного хозяйства:
    # относительные нормировки (IOFCs) считаются внутри хозяйства. Набор
    # культур-кандидатов — тот же, что у солвера: балл обязан сравнивать
    # ровно те варианты, между которыми выбирает модель.
    scorer = _build_factor_scorer(
        df_subset, compat_matrix, planning_year,
        candidate_cultures=sorted(
            (allowed | set(flat_targets)) if allowed else set(flat_targets)
        ) or None,
    )

    return optimize_rotation_with_chemicals(
        df_subset, return_rules, compat_matrix, safe_intervals,
        planning_year=planning_year,
        crop_targets=flat_targets,
        hard_ratio=hard_ratio,
        soft_ratio=soft_ratio,
        weight_compactness=weight_compactness,
        weight_adjacency=weight_adjacency,
        field_overrides=field_overrides,
        factor_scorer=scorer,
        weight_factor_score=weight_factor_score,
        factor_area_weighted=factor_area_weighted,
        hard_block_bad_predecessor=hard_block_bad_predecessor,
        allowed_cultures=allowed,
        max_time=settings.solver_max_time,
        num_workers=settings.solver_workers,
        progress_fn=progress_fn,
    )


def _allowed_cultures() -> set[str]:
    """
    Культуры, которые допустимо назначать: справочник культур плюс нецелевые
    категории землепользования. Пустой набор — фильтрация отключается.
    """
    try:
        from app.core.data_loader import load_cultures_reference
        from app.core.constants import NON_TARGET_CATEGORIES
        reference = load_cultures_reference()
        return (set(reference['culture'].dropna().astype(str))
                | set(NON_TARGET_CATEGORIES))
    except Exception:
        logger.exception('Не удалось прочитать справочник культур')
        return set()


def _build_factor_scorer(df_subset, compat_matrix: dict, planning_year: int,
                         candidate_cultures: list | None = None):
    """
    Собрать FactorScorer для подмножества полей. Любой сбой подсистемы факторов
    не должен ронять расчёт: план без балла лучше, чем отсутствие плана.
    """
    try:
        from app.core.data_loader import load_field_geometries
        from app.core.factors import FactorScorer
        return FactorScorer(
            planning_year=planning_year,
            df_sev=df_subset,
            geometries=load_field_geometries(),
            compat_matrix=compat_matrix,
            candidate_cultures=candidate_cultures,
        )
    except Exception:
        logger.exception('Не удалось построить балльную оценку факторов СПП')
        return None


def _run_solver(request_dict: dict, progress_cb=None) -> dict:
    """Run solver in a worker thread (CPU-bound).

    Стратегия: если crop_targets nested ({farm: {culture: ha}}), цикл по
    каждой ферме отдельно — это резко уменьшает размер задачи (несколько
    маленьких CP-SAT моделей вместо одной огромной) и изолирует возможные
    INFEASIBLE случаи отдельных ферм.

    progress_cb(event: dict) — вызывается из этого (рабочего) потока для
    отправки статусов/прогресса в SSE-поток. Может быть None.
    """
    import time
    import pandas as pd
    from app.core.data_loader import get_all_data
    from app.core.helpers import extract_crop_targets, normalize_crop_targets
    from app.core.chemical import analyze_original_chemical_violations
    from app.core.validation import validate_solution

    df_sev, safe_intervals, return_rules, compat_matrix = get_all_data()

    crop_targets = request_dict.get('crop_targets')
    if crop_targets is None:
        # Дефолт — берём per-farm цели из факта.
        crop_targets = extract_crop_targets(
            df_sev, request_dict['planning_year'], per_farm=True
        )

    targets_nested, is_per_farm = normalize_crop_targets(crop_targets)

    planning_year = request_dict['planning_year']
    hard_ratio = request_dict['hard_ratio']
    soft_ratio = request_dict['soft_ratio']
    weight_compactness = request_dict.get('weight_compactness', 10)
    weight_adjacency = request_dict.get('weight_adjacency', 0)
    field_overrides = request_dict.get('field_overrides') or {}
    weight_factor_score = request_dict.get('weight_factor_score', WEIGHT_FACTOR_SCORE)
    factor_area_weighted = bool(request_dict.get('factor_area_weighted', False))
    hard_block_bad_predecessor = bool(
        request_dict.get('hard_block_bad_predecessor', HARD_BLOCK_BAD_PREDECESSOR)
    )

    # === Цикл по хозяйствам ===
    # Если is_per_farm=False (flat targets под ключом '_global') — fallback
    # к старому поведению: один прогон на всём df_sev.
    plan_chunks: list = []
    per_farm_info: dict[str, dict] = {}
    aggregate = {
        'objective_value': 0,
        'rotation_errors': 0,
        'rotation_cost': 0,
        'deficit_total_percent': 0,
        'deficit_cost': 0,
        'compactness_distinct_pairs': 0,
        'compactness_cost': 0,
        'adjacency_diff_edges': 0,
        'adjacency_total_edges': 0,
        'adjacency_cost': 0,
        'factor_regret_total': 0,
        'factor_cost': 0,
        'hard_blocked_pred_pairs': 0,
        'solve_time': 0.0,
        'deficit_by_culture': {},
        'compactness_weight': weight_compactness,
        'adjacency_weight': weight_adjacency,
        'factor_weight': weight_factor_score,
        'hard_block_pred_enabled': hard_block_bad_predecessor,
    }
    n_failed = 0
    statuses: list[str] = []

    farm_iter = list(targets_nested.items()) if is_per_farm else [(None, targets_nested.get('_global', {}))]
    n_total = len([1 for _, t in farm_iter if t])

    logger.info(
        'Старт расчёта: год=%s, per_farm=%s, хозяйств=%s, hard_ratio=%s, soft_ratio=%s, '
        'компактность=%s, смежность=%s, балл_факторов=%s (по площади=%s), '
        'жёсткий_запрет_предшественника=%s, переопределений_полей=%s',
        planning_year, is_per_farm, n_total, hard_ratio, soft_ratio,
        weight_compactness, weight_adjacency, weight_factor_score,
        factor_area_weighted, hard_block_bad_predecessor, len(field_overrides),
    )

    idx = 0
    for farm_key, farm_targets in farm_iter:
        if not farm_targets:
            continue
        idx += 1
        farm_label = farm_key if farm_key is not None else '_all'
        logger.info('Хозяйство %s/%s «%s»: запуск солвера', idx, n_total, farm_label)
        if progress_cb:
            progress_cb({
                'type': 'status',
                'message': f'Хозяйство {idx}/{n_total}: {farm_label} — решаем...',
            })
        df_optimized, info = _run_solver_single_farm(
            df_sev, return_rules, compat_matrix, safe_intervals,
            farm=farm_key,
            flat_targets=farm_targets,
            planning_year=planning_year,
            hard_ratio=hard_ratio,
            soft_ratio=soft_ratio,
            weight_compactness=weight_compactness,
            weight_adjacency=weight_adjacency,
            field_overrides=field_overrides,
            weight_factor_score=weight_factor_score,
            factor_area_weighted=factor_area_weighted,
            hard_block_bad_predecessor=hard_block_bad_predecessor,
            progress_fn=progress_cb,
        )

        # Жёсткий запрет предшественника может сделать хозяйство нерешаемым:
        # свободный люфт площадей всего ~10%, и запрет вместе с химией и
        # ручными исключениями его съедает. Вместо потери целого хозяйства
        # пересчитываем его с мягким штрафом и помечаем в отчёте.
        if (df_optimized is None and hard_block_bad_predecessor
                and info.get('hard_blocked_pred_pairs')):
            logger.warning(
                'Хозяйство «%s»: %s при жёстком запрете предшественника — '
                'повтор с мягким штрафом', farm_label, info.get('status'),
            )
            if progress_cb:
                progress_cb({
                    'type': 'status',
                    'message': f'Хозяйство {farm_label}: жёсткий запрет '
                               f'предшественника не даёт решения, повтор с мягким штрафом',
                })
            df_optimized, info = _run_solver_single_farm(
                df_sev, return_rules, compat_matrix, safe_intervals,
                farm=farm_key,
                flat_targets=farm_targets,
                planning_year=planning_year,
                hard_ratio=hard_ratio,
                soft_ratio=soft_ratio,
                weight_compactness=weight_compactness,
                weight_adjacency=weight_adjacency,
                field_overrides=field_overrides,
                weight_factor_score=weight_factor_score,
                factor_area_weighted=factor_area_weighted,
                hard_block_bad_predecessor=False,
                progress_fn=progress_cb,
            )
            if df_optimized is not None:
                info['degraded_to_soft_predecessor'] = True

        if df_optimized is None:
            logger.warning('Хозяйство «%s»: без решения, статус=%s, время=%sс',
                           farm_label, info.get('status'), info.get('solve_time'))
            per_farm_info[farm_label] = {'success': False, 'info': info}
            n_failed += 1
            statuses.append(info.get('status', 'INFEASIBLE'))
            if progress_cb:
                reason = info.get('infeasible_reason')
                message = f'Хозяйство {farm_label}: без решения ({info.get("status")})'
                if reason:
                    message = f'{message}. {reason}'
                progress_cb({'type': 'status', 'message': message})
            continue

        logger.info('Хозяйство «%s»: готово, статус=%s, objective=%s, время=%sс',
                    farm_label, info.get('status'), info.get('objective_value'),
                    info.get('solve_time'))
        per_farm_info[farm_label] = {'success': True, 'info': info}
        plan_chunks.append(df_optimized)
        statuses.append(info.get('status', 'OPTIMAL'))

        # Агрегируем числовые показатели
        for k in (
            'objective_value', 'rotation_errors', 'rotation_cost',
            'deficit_total_percent', 'deficit_cost',
            'compactness_distinct_pairs', 'compactness_cost',
            'adjacency_diff_edges', 'adjacency_total_edges', 'adjacency_cost',
            'factor_regret_total', 'factor_cost', 'hard_blocked_pred_pairs',
        ):
            aggregate[k] += info.get(k, 0) or 0
        aggregate['solve_time'] += info.get('solve_time', 0) or 0

        # deficit_by_culture: префиксуем ключи именем фермы для уникальности
        for cult, dinfo in (info.get('deficit_by_culture') or {}).items():
            key = f'{farm_label} / {cult}' if farm_key is not None else cult
            d = dict(dinfo)
            if farm_key is not None:
                d['farm'] = farm_key
            aggregate['deficit_by_culture'][key] = d

    if not plan_chunks:
        named_statuses = [s for s in statuses if s]
        aggregate['status'] = (
            'MIXED' if len(set(named_statuses)) > 1
            else (named_statuses[0] if named_statuses else 'INFEASIBLE')
        )
        aggregate['n_farms'] = 0
        aggregate['n_farms_failed'] = n_failed
        reasons = []
        for label, entry in per_farm_info.items():
            reason = (entry.get('info') or {}).get('infeasible_reason')
            if reason:
                reasons.append(f'{label}: {reason}')
        message = f'Все хозяйства завершились без решения ({n_failed}).'
        if reasons:
            message = f'{message} ' + ' '.join(reasons)
        return {
            'success': False,
            'result_info': aggregate,
            'per_farm_info': per_farm_info,
            'message': message,
        }

    df_all = pd.concat(plan_chunks, ignore_index=True)

    # Средний балл по плану считаем только здесь: складывать средние по
    # хозяйствам нельзя — у них от 2 до 432 полей. Поля без балла
    # (многолетники, ручные исключения) в среднее не входят.
    if 'factor_score' in df_all.columns:
        scored = df_all[df_all['factor_score'].notna()]
        if not scored.empty:
            areas = pd.to_numeric(scored['area_ha'], errors='coerce').fillna(0.0)
            scores = pd.to_numeric(scored['factor_score'], errors='coerce')
            total_area = float(areas.sum())
            aggregate['factor_scored_fields'] = int(len(scored))
            aggregate['factor_score_avg'] = round(float(scores.mean()), 2)
            aggregate['factor_score_area_avg'] = (
                round(float((scores * areas).sum() / total_area), 2)
                if total_area > 0 else None
            )
        else:
            aggregate['factor_scored_fields'] = 0
            aggregate['factor_score_avg'] = None
            aggregate['factor_score_area_avg'] = None

    aggregate['solve_time'] = round(aggregate['solve_time'], 1)
    aggregate['status'] = 'MIXED' if len(set(statuses)) > 1 else (statuses[0] if statuses else 'OPTIMAL')
    aggregate['n_farms'] = len([k for k, v in per_farm_info.items() if v.get('success')])
    aggregate['n_farms_failed'] = n_failed

    # Validate (по всему плану; flat-targets — сумма по культурам)
    if progress_cb:
        progress_cb({'type': 'status', 'message': 'Проверка решения и формирование отчёта...'})
    flat_targets_all: dict[str, float] = {}
    for farm_targets in targets_nested.values():
        for culture, ha in farm_targets.items():
            flat_targets_all[culture] = flat_targets_all.get(culture, 0.0) + ha
    # Вручную исключённые поля — не решение солвера, в валидации не участвуют
    # (иначе их нецелевая категория ловит ложные «нарушения севооборота»).
    df_for_validation = df_all
    if field_overrides:
        _override_ids = {str(k) for k in field_overrides}
        df_for_validation = df_all[~df_all['field_id'].astype(str).isin(_override_ids)]
    _t = time.time()
    is_valid, validation_errors = validate_solution(
        df_for_validation, df_sev, return_rules, compat_matrix,
        safe_intervals, flat_targets_all,
        planning_year=planning_year, hard_ratio=hard_ratio,
    )
    logger.info('Валидация плана: %.1fс, ошибок=%s', time.time() - _t, len(validation_errors))

    # Comparison per (farm, culture)
    comparison: list[dict] = []
    if 'farm' in df_all.columns:
        plan_by_farm = df_all.groupby(['farm', 'culture_unified'])['area_ha'].sum()
    else:
        plan_by_farm = pd.Series(dtype=float)
    for farm_key, farm_targets in targets_nested.items():
        for culture, target in farm_targets.items():
            actual = float(plan_by_farm.get((farm_key, culture), 0.0)) if not plan_by_farm.empty else 0.0
            comparison.append({
                'farm': farm_key if farm_key != '_global' else None,
                'culture': culture,
                'target': target,
                'actual': actual,
                'percent': round(actual / target * 100, 1) if target > 0 else 0,
            })

    # Исходные нарушения химии — по всем хозяйствам, для UI-плашки «до/после»
    _t = time.time()
    original_violations = analyze_original_chemical_violations(
        df_sev, safe_intervals, planning_year
    )
    logger.info('Анализ исходных нарушений химии: %.1fс, нарушений=%s',
                time.time() - _t, len(original_violations))

    # Покрытие факторов данными — то, без чего средний балл нельзя читать:
    # 62 балла по 15% факторов и по 90% факторов означают разное.
    factors_coverage = None
    try:
        from app.core.factors import coverage_report
        factors_coverage = coverage_report(df_sev, compat_matrix, planning_year)
    except Exception:
        logger.exception('Не удалось построить отчёт о покрытии факторов')

    return {
        'success': True,
        'result_info': aggregate,
        'factors_coverage': factors_coverage,
        'optimized_plan': df_all.to_dict(orient='records'),
        'original_violations': original_violations,
        'validation': {'is_valid': is_valid, 'errors': validation_errors},
        'comparison': sorted(comparison, key=lambda x: -x['target']),
        'crop_targets': crop_targets,
        'per_farm': is_per_farm,
        'per_farm_info': per_farm_info,
    }


def _persist_result(result: dict, request: OptimizationRequest) -> None:
    """Сохранить успешный результат на диск + записать строку в лог."""
    from app.core import run_store
    from app.core.helpers import normalize_crop_targets
    import uuid

    run_id = request.run_id or uuid.uuid4().hex[:8]
    targets_nested, _ = normalize_crop_targets(result.get('crop_targets') or {})
    farms = list(targets_nested.keys())
    n_farms = len([f for f in farms if f and f != '_global'])
    info = result.get('result_info') or {}
    logger.info(
        'Прогон %s: хозяйств=%s, год=%s, успех=%s, статус=%s, objective=%s, дефицит=%s%%, время=%sс',
        run_id, n_farms, request.planning_year,
        result.get('success'), info.get('status'), info.get('objective_value'),
        info.get('deficit_total_percent'), info.get('solve_time'),
    )
    if result.get('success'):
        try:
            run_store.save_result(
                result, run_id=run_id, farms=farms,
                planning_year=request.planning_year,
                created_at=datetime.now(timezone.utc),
            )
        except Exception:
            logger.exception('Не удалось сохранить результат %s', run_id)


# Интервал heartbeat (сек). Пока солвер считает, SSE-поток не должен «молчать»:
# иначе обратный прокси (nginx proxy_read_timeout) или браузер рвут соединение,
# и фронт показывает «network error». Должен быть заметно меньше
# proxy_read_timeout на nginx (там 600с).
_HEARTBEAT_SECONDS = 10


def _sse(event: dict) -> str:
    return f"data: {json.dumps(json_safe(event), ensure_ascii=False, allow_nan=False)}\n\n"


@router.post("/run")
async def run_optimization(
    request: OptimizationRequest,
    _user: User = Depends(get_current_user),
):
    """Run optimization with SSE progress streaming."""

    async def event_stream():
        loop = asyncio.get_running_loop()
        # Очередь событий из рабочего потока солвера в этот (event-loop) поток.
        q: asyncio.Queue = asyncio.Queue()
        _SENTINEL = object()

        def progress_cb(event: dict) -> None:
            # Вызывается из рабочего потока (солвер). Переносим событие в loop
            # потокобезопасно. Если loop уже закрыт (клиент отвалился) — игнор.
            try:
                loop.call_soon_threadsafe(q.put_nowait, event)
            except RuntimeError:
                pass

        def run_blocking():
            """Полный расчёт в рабочем потоке. Возвращает ('ok', result) либо
            ('err', message). Результат сохраняем здесь же, чтобы он уцелел даже
            если клиент отключился до конца стрима."""
            global _last_result
            try:
                result = json_safe(_run_solver(request.model_dump(), progress_cb))
                _last_result = result
                try:
                    _persist_result(result, request)
                except Exception:
                    logger.exception('Ошибка при сохранении результата')
                return ('ok', result)
            except Exception as e:
                logger.exception('Оптимизация завершилась ошибкой')
                return ('err', str(e))
            finally:
                loop.call_soon_threadsafe(q.put_nowait, _SENTINEL)

        yield _sse({'type': 'status', 'message': 'Запуск оптимизации...'})

        # Если уже идёт расчёт — сообщаем и ждём очереди (один прогон за раз).
        if _run_lock.locked():
            yield _sse({'type': 'status', 'message': 'Идёт другой расчёт, ожидание очереди...'})

        async with _run_lock:
            future = loop.run_in_executor(None, run_blocking)
            # Дренируем события прогресса; на простое шлём heartbeat-комментарий,
            # чтобы соединение не закрывалось по таймауту во время решения.
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=_HEARTBEAT_SECONDS)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                if event is _SENTINEL:
                    break
                yield _sse(event)
            outcome, payload = await future

        if outcome == 'err':
            yield _sse({'type': 'error', 'message': payload})
        else:
            yield _sse({'type': 'result', 'data': payload})

        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/results")
async def list_saved_results(
    scope: str = "recent",  # "recent" — за последний час; "all" — все
    _user: User = Depends(get_current_user),
):
    """Список сохранённых результатов (meta), новые сверху."""
    from app.core import run_store
    within = 1.0 if scope == "recent" else None
    return run_store.list_results(within_hours=within)


@router.get("/results/{run_id}")
async def get_saved_result(
    run_id: str,
    _user: User = Depends(get_current_user),
):
    """Полный сохранённый результат ({meta, result}) по run_id."""
    from app.core import run_store
    data = run_store.load_result(run_id)
    if not data:
        raise HTTPException(status_code=404, detail="Результат не найден")
    return data


@router.get("/last-result")
async def get_last_result(
    _user: User = Depends(get_current_user),
):
    """Return cached last optimization result."""
    if _last_result is None:
        return {"success": False, "message": "No optimization results yet"}
    return _last_result


class PlanGeoJsonRequest(BaseModel):
    """Тело для POST /plan-geojson-from-data."""
    plan: list[dict]  # каждый элемент имеет хотя бы field_id, culture_unified


def _build_geojson_from_rows(rows: list[dict], geoms: dict) -> dict:
    features = []
    for r in rows:
        fid = r.get('field_id')
        polys = geoms.get(fid)
        if not polys:
            continue
        if len(polys) == 1:
            geometry = {"type": "Polygon",
                        "coordinates": [[[float(x), float(y)] for x, y in polys[0]]]}
        else:
            geometry = {"type": "MultiPolygon",
                        "coordinates": [[[[float(x), float(y)] for x, y in ring]] for ring in polys]}
        features.append({
            "type": "Feature",
            "geometry": geometry,
            "properties": {
                "field_id": fid,
                "culture": r.get('culture_unified') or r.get('culture'),
                "area_ha": float(r.get('area_ha') or 0),
                "farm": r.get('farm'),
                "subdivision": r.get('subdivision'),
            },
        })
    return {
        "type": "FeatureCollection",
        "features": features,
        "n_features": len(features),
    }


@router.post("/plan-geojson-from-data")
async def post_plan_geojson(
    body: PlanGeoJsonRequest,
    _user: User = Depends(get_current_user),
):
    """
    GeoJSON FeatureCollection из произвольного плана (uploaded из файла).
    Каждый элемент plan должен содержать минимум {field_id, culture_unified}.
    Геометрии подтягиваются из fields_catalog.parquet по field_id.
    """
    from app.core.data_loader import load_field_geometries
    geoms = load_field_geometries()
    if not geoms:
        return {"type": "FeatureCollection", "features": [],
                "message": "Геометрии не загружены"}
    return {**_build_geojson_from_rows(body.plan, geoms), "source": "file"}


@router.get("/plan-geojson")
async def get_plan_geojson(
    source: str = "plan",  # "plan" — последний план; "history" — факт planning_year
    planning_year: int = 2026,
    _user: User = Depends(get_current_user),
):
    """
    GeoJSON FeatureCollection с полигонами полей, раскрашенными по культуре.
    Используется фронтендом для карты.
    """
    from app.core.data_loader import load_field_geometries, get_all_data

    geoms = load_field_geometries()
    if not geoms:
        return {"type": "FeatureCollection", "features": [],
                "message": "Геометрии не загружены (fields_catalog.parquet отсутствует)"}

    if source == "plan":
        if _last_result is None or not _last_result.get('success'):
            return {"type": "FeatureCollection", "features": [],
                    "message": "Нет последнего результата оптимизации"}
        rows = _last_result['optimized_plan']
    else:
        df_sev, *_ = get_all_data()
        df = df_sev[df_sev['year'] == planning_year].dropna(subset=['field_id']).drop_duplicates('field_id')
        rows = df[['field_id', 'culture_unified', 'area_ha', 'farm', 'subdivision']].to_dict(orient='records')

    return {**_build_geojson_from_rows(rows, geoms), "source": source}
