"""
Загрузка и кэширование данных из Excel/CSV файлов.
"""
import json
import pandas as pd
from functools import lru_cache
from collections import defaultdict

from app.config import settings
from app.core.constants import CULTURE_MAPPING


@lru_cache(maxsize=1)
def load_sevooborot_with_chem() -> pd.DataFrame:
    """Загрузка севооборота с данными о химобработке."""
    chem_file = settings.data_dir / 'Севооборот_с_химобработкой.xlsx'
    df = pd.read_excel(chem_file, sheet_name='Sheet1', header=0)

    df = df.rename(columns={
        'Сезон': 'year',
        'Хозяйство': 'farm',
        'Подразделение': 'subdivision',
        'Сельское поселение': 'settlement',
        'Полный номер поля': 'field_id',
        'Номер поля (текстовый сокращенный)': 'field_short',
        'Вид с/х угодья': 'land_type',
        'Балл плодородия': 'fertility_score',
        'Культура': 'culture',
        'Площадь (га)': 'area_ha',
        'Название препарата': 'pesticide_name',
        'Действующее вещество': 'active_substance',
        # Нужна фактору «Тип обработки почвы» (см. app/core/factors)
        'Технология выращивания': 'tillage',
    })

    df['year'] = pd.to_numeric(df['year'], errors='coerce')
    df['area_ha'] = pd.to_numeric(df['area_ha'], errors='coerce')

    df['culture_original'] = df['culture']
    df['culture_unified'] = df['culture'].map(
        lambda x: CULTURE_MAPPING.get(x, x) if pd.notna(x) else x
    )

    return df


@lru_cache(maxsize=1)
def load_safe_intervals() -> dict:
    """Загрузка справочника безопасных интервалов."""
    interval_file = settings.data_dir / 'вещество_безопасный_интервал.xlsx'
    df = pd.read_excel(interval_file, sheet_name='Справочник последействия')

    intervals = {}
    for _, row in df.iterrows():
        substance = row['Действующее вещество']
        culture = row['Целевая культура']
        days = row['Безопасный интервал, дней']

        if pd.isna(substance) or pd.isna(culture) or pd.isna(days):
            continue

        try:
            days_int = int(float(days))
        except (ValueError, TypeError):
            continue

        key = (str(substance).lower().strip(), str(culture))
        years = days_int / 365.0
        intervals[key] = years

    return intervals


@lru_cache(maxsize=1)
def load_cultures_reference() -> pd.DataFrame:
    """Загрузка справочника культур (правила возврата)."""
    return pd.read_csv(settings.data_dir / 'cultures_reference.csv')


@lru_cache(maxsize=1)
def load_compatibility_matrix() -> pd.DataFrame:
    """Загрузка матрицы совместимости."""
    return pd.read_csv(settings.data_dir / 'compatibility_matrix.csv', index_col=0)


def get_return_rules(df_cultures: pd.DataFrame) -> dict:
    """Получить правила возврата культур."""
    rules = {}
    for _, row in df_cultures.iterrows():
        culture = row['culture']
        years = row['years_to_return']
        max_consec = row['max_consecutive_years']
        if pd.notna(years):
            rules[culture] = {
                'years_to_return': int(years),
                'max_consecutive': int(max_consec) if pd.notna(max_consec) else 1
            }
    return rules


def get_compatibility_dict(df_compat: pd.DataFrame) -> dict:
    """Получить матрицу совместимости как словарь."""
    compat = {}
    for successor in df_compat.index:
        compat[successor] = {}
        for predecessor in df_compat.columns:
            val = df_compat.loc[successor, predecessor]
            if pd.notna(val):
                compat[successor][predecessor] = val
    return compat


def get_all_data():
    """Загрузить все данные (кэшировано)."""
    df_sev = load_sevooborot_with_chem()
    safe_intervals = load_safe_intervals()
    df_cultures = load_cultures_reference()
    return_rules = get_return_rules(df_cultures)
    df_compat = load_compatibility_matrix()
    compat_matrix = get_compatibility_dict(df_compat)
    return df_sev, safe_intervals, return_rules, compat_matrix


@lru_cache(maxsize=1)
def load_field_geometries() -> dict[str, list]:
    """
    Загрузка WGS84-полигонов полей из data/fields_catalog.parquet.

    Возвращает: {field_id: [[(lon, lat), ...], ...]} — каждое поле как
    список колец (один полигон → одно кольцо; MultiPolygon → несколько).
    Если файла нет — возвращает пустой словарь.
    """
    path = settings.data_dir / 'fields_catalog.parquet'
    if not path.exists():
        return {}
    df = pd.read_parquet(path)
    df = df.drop_duplicates('field_id_full')

    result: dict[str, list] = {}
    for _, row in df.iterrows():
        raw = row['geometry_wgs84']
        if pd.isna(raw):
            continue
        try:
            geom = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        polys = []
        if geom['type'] == 'Polygon':
            polys.append([tuple(pt) for pt in geom['coordinates'][0]])
        elif geom['type'] == 'MultiPolygon':
            for poly in geom['coordinates']:
                polys.append([tuple(pt) for pt in poly[0]])
        else:
            continue
        if polys:
            result[row['field_id_full']] = polys
    return result
