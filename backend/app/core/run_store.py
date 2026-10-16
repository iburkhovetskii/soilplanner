"""
Хранение результатов оптимизации на диске.

Каждый успешный прогон сохраняется в settings.results_dir одним JSON-файлом
вида ``<хозяйство>_<YYYYMMDD_HHMMSS>_<run_id>.json`` со структурой
``{"meta": {...}, "result": {...}}``. Папки логов и результатов
предполагаются смонтированными в образ (volume).
"""
import json
import logging
import re
from datetime import datetime, timedelta, timezone

from app.config import settings
from app.core.helpers import json_safe

logger = logging.getLogger("soil_planner.runs")


def _safe(name: str) -> str:
    """Имя, безопасное для файловой системы (без пробелов и спецсимволов)."""
    cleaned = re.sub(r'[\\/:*?"<>|\s]+', '_', name)
    cleaned = re.sub(r'_+', '_', cleaned).strip('_')
    return cleaned or 'план'


def farm_label(farms: list[str]) -> str:
    """Человекочитаемая подпись набора хозяйств для имени файла."""
    real = [f for f in farms if f and f != '_global']
    if len(real) == 1:
        return real[0]
    if len(real) > 1:
        return 'все_хозяйства'
    return 'план'


def save_result(
    result: dict,
    *,
    run_id: str,
    farms: list[str],
    planning_year: int,
    created_at: datetime,
) -> str | None:
    """Сохранить успешный результат на диск. Возвращает имя файла или None."""
    if not result or not result.get('success'):
        return None

    settings.results_dir.mkdir(parents=True, exist_ok=True)
    info = result.get('result_info') or {}
    meta = {
        'run_id': run_id,
        'created_at': created_at.isoformat(),
        'farms': [f for f in farms if f and f != '_global'],
        'planning_year': planning_year,
        'status': info.get('status'),
        'objective_value': info.get('objective_value'),
        'deficit_total_percent': info.get('deficit_total_percent'),
        'n_fields': len(result.get('optimized_plan') or []),
        # Балл факторов СПП: None у прогонов, сделанных до внедрения модели.
        'factor_score_area_avg': info.get('factor_score_area_avg'),
    }
    fname = f'{_safe(farm_label(farms))}_{created_at.strftime("%Y%m%d_%H%M%S")}_{run_id}.json'
    path = settings.results_dir / fname
    path.write_text(
        json.dumps(
            json_safe({'meta': meta, 'result': result}),
            ensure_ascii=False,
            allow_nan=False,
        ),
        encoding='utf-8',
    )
    logger.info('Результат сохранён: %s', fname)
    return fname


def list_results(within_hours: float | None = None) -> list[dict]:
    """Список meta сохранённых результатов (новые сверху).

    within_hours=None — все; иначе только за последние within_hours часов.
    """
    d = settings.results_dir
    if not d.exists():
        return []
    cutoff = None
    if within_hours is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=within_hours)

    out: list[dict] = []
    for p in sorted(d.glob('*.json')):
        try:
            meta = json.loads(p.read_text(encoding='utf-8')).get('meta', {})
        except (json.JSONDecodeError, OSError):
            continue
        if cutoff is not None:
            dt = _parse_dt(meta.get('created_at'))
            if dt is not None and dt < cutoff:
                continue
        out.append(meta)
    out.sort(key=lambda m: m.get('created_at') or '', reverse=True)
    return out


def load_result(run_id: str) -> dict | None:
    """Загрузить полный сохранённый результат ({meta, result}) по run_id."""
    d = settings.results_dir
    if not d.exists():
        return None
    safe_id = re.sub(r'[^A-Za-z0-9_-]', '', run_id)
    for p in d.glob(f'*_{safe_id}.json'):
        try:
            return json_safe(json.loads(p.read_text(encoding='utf-8')))
        except (json.JSONDecodeError, OSError):
            return None
    return None


def _parse_dt(value) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
