"""
Data endpoints: fields, cultures, farms, subdivisions, crop targets, summary.
"""
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.auth.router import get_current_user, User
from app.core.data_loader import get_all_data
from app.core.helpers import is_perennial, extract_crop_targets
from app.core.chemical import build_field_chemical_history
from app.core.constants import NON_TARGET_CATEGORIES

router = APIRouter(prefix="/api/data", tags=["data"])


class FieldRecord(BaseModel):
    field_id: str
    farm: str | None = None
    subdivision: str | None = None
    culture: str | None = None
    area_ha: float | None = None
    fertility_score: float | None = None
    is_perennial: bool = False


class ChemicalRecord(BaseModel):
    year: int
    substances: list[str]


class SummaryResponse(BaseModel):
    total_fields: int
    total_area: float
    optimizable_fields: int
    optimizable_area: float


@router.get("/fields")
async def get_fields(
    year: int = 2026,
    farm: str | None = None,
    subdivision: str | None = None,
    culture: str | None = None,
    _user: User = Depends(get_current_user),
) -> list[FieldRecord]:
    df_sev, *_ = get_all_data()
    df = df_sev[df_sev['year'] == year].copy()

    if farm:
        df = df[df['farm'] == farm]
    if subdivision:
        df = df[df['subdivision'] == subdivision]
    if culture:
        df = df[df['culture_unified'] == culture]

    # Deduplicate by field_id (chemical rows create duplicates)
    df_dedup = df.drop_duplicates(subset='field_id')

    results = []
    for _, row in df_dedup.iterrows():
        results.append(FieldRecord(
            field_id=str(row['field_id']),
            farm=str(row['farm']) if not _isna(row.get('farm')) else None,
            subdivision=str(row['subdivision']) if not _isna(row.get('subdivision')) else None,
            culture=str(row['culture_unified']) if not _isna(row.get('culture_unified')) else None,
            area_ha=float(row['area_ha']) if not _isna(row.get('area_ha')) else None,
            fertility_score=float(row['fertility_score']) if not _isna(row.get('fertility_score')) else None,
            is_perennial=is_perennial(row.get('culture_unified')),
        ))
    return results


@router.get("/farm-fields")
async def get_farm_fields(
    farm: str,
    planning_year: int = 2026,
    _user: User = Depends(get_current_user),
) -> list[FieldRecord]:
    """
    Поля хозяйства из «актуального снимка» — последнего года с данными
    ≤ planning_year (та же логика, что и в солвере). Нужно для списка
    исключения полей: у части хозяйств в самом свежем глобальном году данных
    нет (напр. «Защитное Юг» заканчивается на 2025), и фильтр по фиксированному
    году вернул бы пусто.
    """
    df_sev, *_ = get_all_data()
    d = df_sev[df_sev['farm'] == farm].dropna(subset=['field_id'])
    if d.empty:
        return []

    years_all = [int(y) for y in d['year'].dropna().unique()]
    years_le = [y for y in years_all if y <= planning_year]
    snapshot_year = max(years_le) if years_le else max(years_all)

    df_dedup = d[d['year'] == snapshot_year].drop_duplicates(subset='field_id')
    results = []
    for _, row in df_dedup.iterrows():
        results.append(FieldRecord(
            field_id=str(row['field_id']),
            farm=str(row['farm']) if not _isna(row.get('farm')) else None,
            subdivision=str(row['subdivision']) if not _isna(row.get('subdivision')) else None,
            culture=str(row['culture_unified']) if not _isna(row.get('culture_unified')) else None,
            area_ha=float(row['area_ha']) if not _isna(row.get('area_ha')) else None,
            fertility_score=float(row['fertility_score']) if not _isna(row.get('fertility_score')) else None,
            is_perennial=is_perennial(row.get('culture_unified')),
        ))
    return results


@router.get("/fields/{field_id}/chemical-history")
async def get_chemical_history(
    field_id: str,
    _user: User = Depends(get_current_user),
) -> list[ChemicalRecord]:
    df_sev, *_ = get_all_data()
    chem_history = build_field_chemical_history(df_sev)

    result = []
    if field_id in chem_history:
        for year, substances in sorted(chem_history[field_id].items(), reverse=True):
            result.append(ChemicalRecord(year=year, substances=substances))
    return result


@router.get("/cultures")
async def get_cultures(
    year: int | None = None,
    farm: str | None = None,
    optimizable_only: bool = False,
    _user: User = Depends(get_current_user),
) -> list[str]:
    """
    Список культур.
    year=None — все исторические культуры (для UI «добавить культуру в план»).
    optimizable_only=True — отфильтровать многолетние (Люцерна, Эспарцет и т.п.).
    """
    from app.core.helpers import is_perennial
    from app.core.data_loader import load_cultures_reference
    df_sev, *_ = get_all_data()
    df = df_sev
    if year is not None:
        df = df[df['year'] == year]
    if farm:
        df = df[df['farm'] == farm]
    cultures = df['culture_unified'].dropna().unique().tolist()
    if optimizable_only:
        # Фильтруем по справочнику культур: оставляем только нормированные имена
        # (без мусора типа '-', '0', чисел и т.п.).
        df_cult_ref = load_cultures_reference()
        ref_set = set(df_cult_ref['culture'].dropna().astype(str))
        cultures = [c for c in cultures
                    if c in ref_set and not is_perennial(c)]
    return sorted(cultures)


@router.get("/non-target-categories")
async def get_non_target_categories(
    _user: User = Depends(get_current_user),
) -> list[str]:
    """
    Нецелевые категории землепользования (не культуры): «Пар», «Строительство»
    и т.п. Используются на странице планирования, чтобы вручную исключить поле
    из солвера и записать ему такую категорию в итоговый план.
    """
    return list(NON_TARGET_CATEGORIES)


@router.get("/crop-colors")
async def get_crop_colors(
    year: int | None = None,
    _user: User = Depends(get_current_user),
) -> dict:
    """
    Палитра культур из справочника заказчика (crop_colors.csv): цвет, группа
    и категория для каждой культуры, встречающейся в данных.

    year=None — все культуры истории плюс нецелевые категории. Культуры, для
    которых в справочнике нет записи, возвращаются с matched=false и цветом из
    запасной серой палитры: фронтенд обязан отличать «серый по справочнику» от
    «серый, потому что не нашли».
    """
    from app.core.crops import FALLBACK_COLOR, get_taxonomy

    df_sev, *_ = get_all_data()
    df = df_sev if year is None else df_sev[df_sev['year'] == year]

    cultures = set(df['culture_unified'].dropna().astype(str))
    # Исходные названия тоже кладём в палитру: карта и выгрузки могут показывать
    # культуру до унификации, а сопоставление по «сырому» имени точнее.
    if 'culture_original' in df.columns:
        cultures |= set(df['culture_original'].dropna().astype(str))
    cultures |= set(NON_TARGET_CATEGORIES)

    taxonomy = get_taxonomy()
    colors = taxonomy.palette(sorted(cultures))
    return {
        "fallback": FALLBACK_COLOR,
        "reference_size": len(taxonomy.reference),
        "matched": sum(1 for v in colors.values() if v["matched"]),
        "total": len(colors),
        "colors": colors,
    }


@router.get("/years")
async def get_years(
    _user: User = Depends(get_current_user),
) -> list[int]:
    """Доступные годы в данных (по убыванию)."""
    df_sev, *_ = get_all_data()
    years = sorted(df_sev['year'].dropna().unique().tolist(), reverse=True)
    return [int(y) for y in years]


@router.get("/farms")
async def get_farms(
    year: int | None = None,
    _user: User = Depends(get_current_user),
) -> list[str]:
    df_sev, *_ = get_all_data()
    df = df_sev
    if year is not None:
        df = df[df['year'] == year]
    return sorted(df['farm'].dropna().unique().tolist())


@router.get("/subdivisions")
async def get_subdivisions(
    farm: str | None = None,
    _user: User = Depends(get_current_user),
) -> list[str]:
    df_sev, *_ = get_all_data()
    df = df_sev
    if farm:
        df = df[df['farm'] == farm]
    return sorted(df['subdivision'].dropna().unique().tolist())


@router.get("/crop-targets")
async def get_crop_targets(
    year: int = 2026,
    per_farm: bool = False,
    _user: User = Depends(get_current_user),
) -> dict:
    """
    per_farm=False (default): {culture: ha}
    per_farm=True:            {farm: {culture: ha}}
    """
    df_sev, *_ = get_all_data()
    return extract_crop_targets(df_sev, year, per_farm=per_farm)


@router.get("/summary")
async def get_summary(
    year: int = 2026,
    farm: str | None = None,
    _user: User = Depends(get_current_user),
) -> SummaryResponse:
    df_sev, *_ = get_all_data()
    df = df_sev[df_sev['year'] == year]
    if farm:
        df = df[df['farm'] == farm]
    df = df.drop_duplicates(subset='field_id')

    opt_mask = ~df['culture_unified'].apply(is_perennial)
    return SummaryResponse(
        total_fields=len(df),
        total_area=float(df['area_ha'].sum()),
        optimizable_fields=int(opt_mask.sum()),
        optimizable_area=float(df.loc[opt_mask, 'area_ha'].sum()),
    )


def _isna(val) -> bool:
    import pandas as pd
    try:
        return pd.isna(val)
    except (TypeError, ValueError):
        return val is None
