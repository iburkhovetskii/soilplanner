"""
Генерация Excel/CSV отчётов.
"""
import pandas as pd
from io import BytesIO


def generate_csv(df_optimized: pd.DataFrame) -> bytes:
    """Генерация CSV с оптимизированным планом."""
    buffer = BytesIO()
    df_optimized.to_csv(buffer, index=False, encoding='utf-8-sig')
    return buffer.getvalue()


def generate_excel(
    df_optimized: pd.DataFrame,
    crop_targets: dict,
    deficit_info: dict,
) -> bytes:
    """Генерация Excel отчёта (3 листа)."""
    buffer = BytesIO()

    with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
        # Лист 1: План
        df_optimized.to_excel(writer, sheet_name='План', index=False)

        # Лист 2: Сравнение
        plan_areas = df_optimized.groupby('culture_unified')['area_ha'].sum()
        df_comparison = pd.DataFrame({
            'Культура': list(crop_targets.keys()),
            'План (га)': [crop_targets.get(c, 0) for c in crop_targets.keys()],
            'Факт (га)': [plan_areas.get(c, 0) for c in crop_targets.keys()]
        })
        df_comparison['Выполнение (%)'] = (
            df_comparison['Факт (га)'] / df_comparison['План (га)'] * 100
        ).round(1)
        df_comparison.to_excel(writer, sheet_name='Сравнение', index=False)

        # Лист 3: Недоборы
        if deficit_info:
            pd.DataFrame([
                {'Культура': c, **info}
                for c, info in deficit_info.items()
            ]).to_excel(writer, sheet_name='Недоборы', index=False)

    return buffer.getvalue()
