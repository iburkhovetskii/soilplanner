"""Синтетические данные для тестов: та же форма, что у реальных загрузчиков."""
import pandas as pd

FARM_A = "Ферма-А"
FARM_B = "Ферма-Б"

WHEAT = "Яр.пшеница мягкая"
PEA = "Горох"
SUNFLOWER = "Подсолнечник"
MAIZE = "Кукуруза силос"

# история: field_id -> {year: culture}
_HISTORY = {
    # Ферма-А, отделение А1 (100 га каждое)
    "A-01": {2023: PEA, 2024: WHEAT, 2025: SUNFLOWER, 2026: WHEAT},
    "A-02": {2023: SUNFLOWER, 2024: WHEAT, 2025: PEA, 2026: WHEAT},
    "A-03": {2023: WHEAT, 2024: MAIZE, 2025: WHEAT, 2026: PEA},
    # Ферма-А, отделение А2
    "A-04": {2023: MAIZE, 2024: PEA, 2025: WHEAT, 2026: SUNFLOWER},
    "A-05": {2023: PEA, 2024: SUNFLOWER, 2025: WHEAT, 2026: MAIZE},
    # многолетка — не оптимизируется, копируется в план как есть
    "A-06": {2023: "Люцерна пр.лет сем.", 2024: "Люцерна пр.лет сем.",
             2025: "Люцерна пр.лет сем.", 2026: "Люцерна пр.лет сем."},
    # Ферма-Б (50 га каждое)
    "B-01": {2023: WHEAT, 2024: PEA, 2025: WHEAT, 2026: SUNFLOWER},
    "B-02": {2023: PEA, 2024: WHEAT, 2025: SUNFLOWER, 2026: WHEAT},
    "B-03": {2023: SUNFLOWER, 2024: WHEAT, 2025: PEA, 2026: WHEAT},
    "B-04": {2023: WHEAT, 2024: SUNFLOWER, 2025: WHEAT, 2026: PEA},
}

# field_id -> (хозяйство, подразделение, площадь, технология выращивания)
_META = {
    "A-01": (FARM_A, "Отд-А1", 100.0, "классическая с плугом"),
    "A-02": (FARM_A, "Отд-А1", 100.0, "минимальная"),
    "A-03": (FARM_A, "Отд-А1", 100.0, "нулевая с прямым посевом"),
    "A-04": (FARM_A, "Отд-А2", 100.0, None),
    "A-05": (FARM_A, "Отд-А2", 100.0, "минимальная"),
    "A-06": (FARM_A, "Отд-А2", 100.0, "минимальная"),
    "B-01": (FARM_B, "Отд-Б1", 50.0, "классическая с плугом"),
    "B-02": (FARM_B, "Отд-Б1", 50.0, "минимальная"),
    "B-03": (FARM_B, "Отд-Б1", 50.0, "минимальная"),
    "B-04": (FARM_B, "Отд-Б1", 50.0, None),
}

# {(field_id, year): "вещество; вещество"} — как в реальном файле (разделитель ';')
_CHEM = {
    ("A-01", 2026): "имазетапир; трибенурон-метил",
    ("B-01", 2026): "имазетапир",
}


def build_df_sev(years=(2023, 2024, 2025, 2026), duplicate_chem_rows=True) -> pd.DataFrame:
    """df_sev той же формы, что отдаёт load_sevooborot_with_chem().

    На одно (field_id, year) может быть НЕСКОЛЬКО строк — как в реальных
    данных, где строка = «поле × химобработка»: это ловит баги дедупликации.
    """
    rows = []
    for field_id, hist in _HISTORY.items():
        farm, subdivision, area, tillage = _META[field_id]
        for year in years:
            culture = hist.get(year)
            if culture is None:
                continue
            substances = _CHEM.get((field_id, year))
            row = {
                "year": year, "farm": farm, "subdivision": subdivision,
                "field_id": field_id, "culture": culture,
                "culture_original": culture, "culture_unified": culture,
                "area_ha": area, "fertility_score": 60.0,
                "tillage": tillage, "active_substance": substances,
            }
            rows.append(row)
            if duplicate_chem_rows and substances:
                rows.append({**row, "active_substance": "глифосат"})
    df = pd.DataFrame(rows)
    df["year"] = df["year"].astype("int64")
    df["area_ha"] = df["area_ha"].astype("float64")
    return df


def build_return_rules() -> dict:
    return {
        WHEAT: {"years_to_return": 2, "max_consecutive": 2},
        PEA: {"years_to_return": 4, "max_consecutive": 1},
        SUNFLOWER: {"years_to_return": 4, "max_consecutive": 1},
        MAIZE: {"years_to_return": 2, "max_consecutive": 3},
    }


def build_compat_matrix() -> dict:
    """{последователь: {предшественник: 0.0|0.2|0.7|1.0}}; 0.0 = запрет."""
    return {
        WHEAT: {WHEAT: 0.2, PEA: 1.0, SUNFLOWER: 0.7, MAIZE: 0.7},
        PEA: {WHEAT: 1.0, PEA: 0.0, SUNFLOWER: 0.2, MAIZE: 0.7},
        SUNFLOWER: {WHEAT: 0.7, PEA: 0.7, SUNFLOWER: 0.0, MAIZE: 0.2},
        MAIZE: {WHEAT: 1.0, PEA: 1.0, SUNFLOWER: 0.2, MAIZE: 0.7},
    }


def build_safe_intervals() -> dict:
    """{(вещество_lower, культура_справочника): интервал_в_годах}."""
    return {
        ("имазетапир", SUNFLOWER): 2.0,          # блокирует в t+1
        ("трибенурон-метил", WHEAT): 0.5,        # НЕ блокирует
        ("глифосат", PEA): 0.1,
    }


def build_crop_targets(per_farm=True) -> dict:
    if per_farm:
        return {
            FARM_A: {WHEAT: 200.0, PEA: 100.0, SUNFLOWER: 100.0},
            FARM_B: {WHEAT: 100.0, PEA: 50.0, SUNFLOWER: 50.0},
        }
    return {WHEAT: 300.0, PEA: 150.0, SUNFLOWER: 150.0}


# --- мини-реестр факторов СПП (пишется в tmp_path, реальные CSV не нужны) ---
FACTORS_CSV = (
    "id,order,category,name,data_type,role,role_raw,weight,norm_kind,unit,"
    "data_source,description,notes\n"
    "soil_ph,1,1. СЗР и Химия почв,Водородный показатель (pH),Float,modifier,"
    "Динамический модификатор,60,numeric,pH,field_soil.csv,тест,\n"
    "predecessor_value,2,5. Агротехника,Ценность как предшественника,Float,bonus,"
    "Долгосрочный оптимизатор,40,flag,,история,тест,\n"
)
BANDS_CSV = (
    "factor_id,order,texture,raw_range,lo,hi,zone,k,max_weight,score\n"
    "soil_ph,1,,< 5.5,-inf,5.5,Кислая,0.0,60,0.0\n"
    "soil_ph,2,,5.5 - 5.9,5.5,6,Слабокислая,0.5,60,30.0\n"
    "soil_ph,3,,6.0 - 7.0,6,7.1,Нейтральная (ОПТИМУМ),1.0,60,60.0\n"
    "soil_ph,4,,> 7.0,7.1,inf,Щелочная,0.3,60,18.0\n"
    "predecessor_value,1,,Бобовые культуры в истории,-inf,inf,Накопление азота,1,40,40\n"
)
ENUM_CSV = "factor_id,value,k,maps_to,note\nsoil_texture,глина,1.0,heavy,тест\n"


def write_mini_registry(tmp_path):
    """Разложить мини-реестр в каталог; вернуть путь для load_registry(data_dir=...)."""
    (tmp_path / "factors_spp.csv").write_text(FACTORS_CSV, encoding="utf-8")
    (tmp_path / "factors_bands.csv").write_text(BANDS_CSV, encoding="utf-8")
    (tmp_path / "factors_enum.csv").write_text(ENUM_CSV, encoding="utf-8")
    return tmp_path


def write_soil(tmp_path, rows: dict) -> None:
    """rows: {field_id: {'ph': 6.5, ...}} → field_soil.csv в каталоге данных."""
    columns = ["field_id", "ph", "cec", "humus_percent", "ca_mg_ratio",
               "texture", "n_mg_kg", "p_mg_kg", "k_mg_kg"]
    lines = [",".join(columns)]
    for field_id, values in rows.items():
        lines.append(",".join(
            [str(field_id)] + [str(values.get(c, "")) for c in columns[1:]]
        ))
    (tmp_path / "field_soil.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
