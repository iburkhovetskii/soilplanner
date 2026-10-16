"""
Эндпоинты по факторам формирования СПП: реестр, покрытие данными, расшифровка
балла конкретной пары «поле → культура».
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth.router import get_current_user, User
from app.core.data_loader import get_all_data, load_field_geometries

router = APIRouter(prefix="/api/factors", tags=["factors"])

logger = logging.getLogger("soil_planner.factors")


@router.get("")
async def get_factors(
    _user: User = Depends(get_current_user),
) -> dict:
    """
    Реестр факторов из «Факторы_формирования_СПП_*.xlsx»: 24 фактора, веса
    (в сумме 100), роли и вилки нормализации. Источник — backend/data/
    factors_spp.csv и factors_bands.csv (генерируются из xlsx скриптом
    backend/scripts/build_factors_registry.py).
    """
    from app.core.factors import get_registry

    registry = get_registry()
    factors = []
    for factor in registry.all:
        factors.append({
            "id": factor.id,
            "order": factor.order,
            "category": factor.category,
            "name": factor.name,
            "data_type": factor.data_type,
            "role": factor.role,
            "role_raw": factor.role_raw,
            "weight": factor.weight,
            "norm_kind": factor.norm_kind,
            "unit": factor.unit,
            "data_source": factor.data_source,
            "description": factor.description,
            "notes": factor.notes,
            "bands": [
                {
                    "order": band.order,
                    "texture": band.texture,
                    "range": band.raw_range,
                    "lo": None if band.lo == float("-inf") else band.lo,
                    "hi": None if band.hi == float("inf") else band.hi,
                    "zone": band.zone,
                    "k": band.k,
                    "score": band.score,
                }
                for band in factor.bands
            ],
            "enum_values": [
                {"value": value, "k": k, "maps_to": maps_to or None, "note": note}
                for value, k, maps_to, note in factor.enum_map
            ],
        })
    return {
        "total_weight": registry.total_weight,
        "count": len(registry),
        "factors": factors,
    }


@router.get("/coverage")
async def get_factors_coverage(
    planning_year: int = 2027,
    _user: User = Depends(get_current_user),
) -> dict:
    """
    Какие факторы реально обеспечены данными: статус по каждому фактору, доля
    полей с данными и суммарный вес доступных факторов. Именно это число
    показывается рядом с баллом плана — «балл посчитан по N% факторов».
    """
    from app.core.factors import coverage_report

    df_sev, _safe_intervals, _return_rules, compat_matrix = get_all_data()
    return coverage_report(df_sev, compat_matrix, planning_year)


@router.get("/explain")
async def explain_score(
    field_id: str,
    culture: str,
    planning_year: int = 2027,
    _user: User = Depends(get_current_user),
) -> dict:
    """
    Расшифровка балла пары «поле → культура»: вклад каждого фактора, какие
    факторы посчитаны, какие выбыли и почему.
    """
    from app.core.factors import FactorScorer

    df_sev, _safe_intervals, _return_rules, compat_matrix = get_all_data()
    subset = df_sev[df_sev["field_id"].astype(str) == str(field_id)]
    if subset.empty:
        raise HTTPException(status_code=404, detail=f"Поле {field_id} не найдено")

    scorer = FactorScorer(
        planning_year=planning_year,
        df_sev=subset,
        geometries=load_field_geometries(),
        compat_matrix=compat_matrix,
    )
    breakdown = scorer.score(field_id, culture)
    facts = scorer.facts.get(str(field_id))
    return {
        "field_id": breakdown.field_id,
        "culture": breakdown.culture,
        "planning_year": planning_year,
        "score": breakdown.score,
        "estimated": breakdown.estimated,
        "points": breakdown.points,
        "available_weight": breakdown.available_weight,
        "scoring_weight": breakdown.scoring_weight,
        "coverage": round(breakdown.coverage, 4),
        "residue_multiplier": scorer.residue_multiplier(field_id),
        "predecessor": facts.predecessor(planning_year) if facts else None,
        "farm": facts.farm if facts else None,
        "subdivision": facts.subdivision if facts else None,
        "tillage": facts.tillage if facts else None,
        "factors": [
            {
                "id": value.factor_id,
                "name": value.name,
                "category": value.category,
                "role": value.role,
                "weight": value.weight,
                "k": value.k,
                "points": round(value.points, 3),
                "raw": value.raw,
                "zone": value.zone,
                "available": value.available,
                "reason": value.reason,
            }
            for value in breakdown.values
        ],
    }


@router.get("/score")
async def score_field(
    field_id: str,
    planning_year: int = 2027,
    cultures: list[str] | None = Query(default=None),
    _user: User = Depends(get_current_user),
) -> dict:
    """
    Балл поля по всем культурам (или по указанному списку) — рейтинг «что
    выгоднее посеять на этом поле по факторам СПП».
    """
    from app.core.factors import FactorScorer
    from app.core.helpers import is_perennial

    df_sev, _safe_intervals, _return_rules, compat_matrix = get_all_data()
    subset = df_sev[df_sev["field_id"].astype(str) == str(field_id)]
    if subset.empty:
        raise HTTPException(status_code=404, detail=f"Поле {field_id} не найдено")

    if not cultures:
        from app.core.data_loader import load_cultures_reference
        reference = load_cultures_reference()
        cultures = [c for c in reference["culture"].dropna().astype(str)
                    if not is_perennial(c)]

    scorer = FactorScorer(
        planning_year=planning_year,
        df_sev=subset,
        geometries=load_field_geometries(),
        compat_matrix=compat_matrix,
    )
    scored = []
    for culture in cultures:
        breakdown = scorer.score(field_id, culture)
        scored.append({
            "culture": culture,
            "score": breakdown.score,
            "estimated": breakdown.estimated,
            "coverage": round(breakdown.coverage, 4),
        })
    scored.sort(key=lambda row: -row["score"])
    return {"field_id": str(field_id), "planning_year": planning_year,
            "cultures": scored}
