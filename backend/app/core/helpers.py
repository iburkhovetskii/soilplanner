"""
Вспомогательные функции.
"""
import math
import re

import pandas as pd

# Год жизни многолетней культуры в любом написании: «1 г.ж.», «2 г.ж», «3г.ж.».
# Раньше маркер был захардкожен как '1 г.ж.', и травы 2-5 годов жизни
# считались однолетними: 95 названий / 1 854 записи / ~122 тыс. га истории
# («Люцерна 2 г.ж.корм», «Кл.кр.+тим.2 г.ж.корм» и т.п.) попадали в
# оптимизируемые поля и раздували целевые площади в extract_crop_targets.
_YEAR_OF_LIFE_RE = re.compile(r"\d+\s*г\.?\s*ж\.?")

_PERENNIAL_MARKERS = ('пр.лет', 'пр. лет', 'Залежь', 'Пастбище', 'Луг',
                      'Не используется')


def is_perennial(culture) -> bool:
    """Проверка многолетней культуры."""
    if pd.isna(culture):
        return True
    text = str(culture)
    if any(m in text for m in _PERENNIAL_MARKERS):
        return True
    return bool(_YEAR_OF_LIFE_RE.search(text))


def extract_crop_targets(
    df_sev,
    year: int = 2026,
    per_farm: bool = False,
) -> dict:
    """
    Извлечь целевые площади по культурам из данных года.

    per_farm=False (по умолчанию, обратная совместимость):
        {культура: площадь_га}  -- глобально по всему набору
    per_farm=True:
        {хозяйство: {культура: площадь_га}}
    """
    # На одно (field_id, year) бывает несколько строк (по числу химобработок).
    # Дедуплицируем по полю, иначе target раздуется.
    df_year = (
        df_sev[df_sev['year'] == year]
        .dropna(subset=['field_id'])
        .drop_duplicates(subset=['field_id'])
    )

    if not per_farm:
        targets: dict[str, float] = {}
        for culture in df_year['culture_unified'].unique():
            if pd.isna(culture) or is_perennial(culture):
                continue
            area = df_year[df_year['culture_unified'] == culture]['area_ha'].sum()
            if area > 0:
                targets[culture] = float(area)
        return targets

    # per-farm режим. Для каждой фермы используем её последний доступный
    # год ≤ запрошенного. Так на странице целей всегда показываются все
    # фермы, даже если в запрошенном году данных у части хозяйств нет.
    out: dict[str, dict[str, float]] = {}
    all_farms = df_sev['farm'].dropna().unique()
    df_dedup = df_sev.dropna(subset=['field_id']).drop_duplicates(
        subset=['field_id', 'year']
    )
    for farm in sorted(all_farms):
        df_farm = df_dedup[df_dedup['farm'] == farm]
        if df_farm.empty:
            continue
        farm_years = df_farm['year'].dropna().unique()
        years_available = sorted([int(y) for y in farm_years if int(y) <= year])
        if not years_available:
            continue
        last_year = years_available[-1]
        df_farm_year = df_farm[df_farm['year'] == last_year]
        farm_targets: dict[str, float] = {}
        for culture in df_farm_year['culture_unified'].unique():
            if pd.isna(culture) or is_perennial(culture):
                continue
            area = df_farm_year[df_farm_year['culture_unified'] == culture]['area_ha'].sum()
            if area > 0:
                farm_targets[culture] = float(area)
        if farm_targets:
            out[str(farm)] = farm_targets
    return out


def json_safe(value):
    """Заменить NaN/Infinity на None, чтобы ответ был обычным JSON.

    DataFrame.to_dict превращает пустые баллы полей в NaN. Стандартный
    json.dumps пишет их как токен NaN, браузер такой ответ не разбирает,
    и интерфейс остаётся в состоянии «идёт расчёт».
    """
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def normalize_crop_targets(crop_targets) -> tuple[dict[str, dict[str, float]], bool]:
    """
    Привести crop_targets к каноническому nested виду {farm: {culture: ha}}.

    Возвращает (nested, is_per_farm_input):
      - если на входе flat {culture: ha} -- nested будет {'_global': {culture: ha}},
        is_per_farm_input=False (значит ограничения применяются глобально).
      - если на входе уже nested -- возвращает как есть, is_per_farm_input=True.
    """
    if not crop_targets:
        return {}, False
    sample_val = next(iter(crop_targets.values()))
    if isinstance(sample_val, dict):
        return crop_targets, True
    return {'_global': dict(crop_targets)}, False
