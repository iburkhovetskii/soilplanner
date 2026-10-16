"""
Export endpoints: CSV, Excel.
"""
import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from app.auth.router import get_current_user, User
from app.core.report import generate_csv, generate_excel

router = APIRouter(prefix="/api/export", tags=["export"])


class ExportRequest(BaseModel):
    optimized_plan: list[dict]
    # Поддерживаем оба формата: flat {culture: ha} и nested {farm: {culture: ha}}
    crop_targets: dict
    deficit_info: dict | None = None


class PlanImageRequest(BaseModel):
    optimized_plan: list[dict]
    farm: str | None = None  # None → панель на каждое хозяйство плана
    planning_year: int | None = None


def _flatten_targets(crop_targets: dict) -> dict[str, float]:
    """Привести nested {farm: {culture: ha}} к flat {culture: ha} суммированием."""
    if not crop_targets:
        return {}
    sample = next(iter(crop_targets.values()))
    if isinstance(sample, dict):
        out: dict[str, float] = {}
        for farm_targets in crop_targets.values():
            for culture, ha in farm_targets.items():
                out[culture] = out.get(culture, 0.0) + float(ha)
        return out
    return {k: float(v) for k, v in crop_targets.items()}


@router.post("/csv")
async def export_csv(
    request: ExportRequest,
    _user: User = Depends(get_current_user),
):
    df = pd.DataFrame(request.optimized_plan)
    if df.empty:
        raise HTTPException(status_code=400, detail="No data to export")

    content = generate_csv(df)
    return Response(
        content=content,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=optimized_plan_2026.csv"},
    )


@router.post("/excel")
async def export_excel(
    request: ExportRequest,
    _user: User = Depends(get_current_user),
):
    df = pd.DataFrame(request.optimized_plan)
    if df.empty:
        raise HTTPException(status_code=400, detail="No data to export")

    content = generate_excel(df, _flatten_targets(request.crop_targets), request.deficit_info or {})
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=optimized_plan_2026.xlsx"},
    )


# Синхронный (def) — matplotlib-рендер CPU-bound; FastAPI выполнит его в пуле
# потоков и не заблокирует event loop.
@router.post("/plan-image")
def export_plan_image(
    request: PlanImageRequest,
    _user: User = Depends(get_current_user),
):
    """PNG с картой полей плана (по хозяйствам), окраска по культуре."""
    if not request.optimized_plan:
        raise HTTPException(status_code=400, detail="No data to export")
    from app.export.plot import render_plan_png
    png = render_plan_png(
        request.optimized_plan,
        farm=request.farm,
        planning_year=request.planning_year,
    )
    return Response(
        content=png,
        media_type="image/png",
        headers={"Content-Disposition": "inline; filename=plan.png"},
    )
