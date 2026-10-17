"""
RAT-бэктест: план сезона по истории до предыдущего года против фактического
(ручного) плана того же сезона, оба проверяются одним валидатором.

Цели по культурам — фактическая структура сезона. Внесения СЗР в самом сезоне
убираются из истории: план не должен знать о будущих обработках.

Запуск из корня репозитория (нужны данные в backend/data):
    .venv/bin/python experiments/rat_backtest.py
    .venv/bin/python experiments/rat_backtest.py --farms "ЭНА Северное" --max-time 20
Результат: experiments/results/rat_backtest_<год>.csv
"""
import argparse
import csv
import sys
import time
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.config import settings  # noqa: E402
from app.core.constants import NON_TARGET_CATEGORIES  # noqa: E402
from app.core.data_loader import get_all_data  # noqa: E402
from app.core.helpers import extract_crop_targets  # noqa: E402
from app.core.validation import validate_solution  # noqa: E402
from app.optimization.router import _run_solver_single_farm  # noqa: E402

RAT_FARMS = [
    "Северная Нива Оренбуржье",
    "ЭНА Южное",
    "КН Запад",
    "Ступинская Нива",
    "Северная Нива Башкирия",
    "ЭНА Северное",
    "КН Восток",
    "ЭНА Восточное",
    "ЭНА Правобережное",
    "ОкаМолоко Север",
    "ЭНА Запад",
]
RESULTS_DIR = REPO_ROOT / "experiments" / "results"
RULES = {
    "Нарушение хим. интервала": "chem",
    "Ранний возврат": "early_return",
    "Повтор культуры подряд": "repeat",
    "Неподходящий предшественник": "predecessor",
}


def without_season_chemistry(df_sev, year: int):
    df = df_sev.copy()
    df.loc[df["year"] == year, "active_substance"] = None
    return df


def manual_plan(df_sev, farm: str, year: int):
    rows = df_sev[(df_sev["farm"] == farm) & (df_sev["year"] == year)]
    return (rows.dropna(subset=["field_id"])
                .drop_duplicates("field_id")[["field_id", "culture_unified", "area_ha"]])


def rule_errors(plan, data, year: int) -> Counter:
    """Нарушения правил без проверки площадей; аренда, стройка и т. п. не считаются."""
    df_sev, safe_intervals, return_rules, compat_matrix = data
    plan = plan[~plan["culture_unified"].isin(NON_TARGET_CATEGORIES)]
    _, errors = validate_solution(
        plan, df_sev, return_rules, compat_matrix, safe_intervals,
        crop_targets={}, planning_year=year,
    )
    return Counter(RULES[e["type"]] for e in errors if e["type"] in RULES)


def micro_targets(targets: dict) -> set[str]:
    """Цели, у которых жёсткий минимум 90% округляется солвером до 0 га."""
    return {c for c, t in targets.items() if t > 0 and int(t * 0.90) == 0}


def area_fulfilment(plan, targets: dict) -> tuple[float, float]:
    """(% выполнения программы в целом, минимум по культуре без микро-целей).

    Перебор площади не засчитывается.
    """
    actual = plan.groupby("culture_unified")["area_ha"].sum()
    done = {c: min(float(actual.get(c, 0.0)), t) for c, t in targets.items() if t > 0}
    total = sum(done.values()) / sum(t for t in targets.values() if t > 0)
    tiny = micro_targets(targets)
    worst = min(done[c] / targets[c] for c in done if c not in tiny)
    return round(total * 100, 1), round(worst * 100, 1)


def run_farm(farm: str, year: int, data, args) -> dict:
    df_sev, safe_intervals, return_rules, compat_matrix = data
    targets = extract_crop_targets(df_sev, year, per_farm=True).get(farm)
    if not targets:
        raise SystemExit(f"Нет целей для «{farm}» в {year} году")

    events: list[dict] = []
    started = time.time()
    plan, info = _run_solver_single_farm(
        df_sev, return_rules, compat_matrix, safe_intervals,
        farm=farm, flat_targets=targets, planning_year=year,
        hard_ratio=0.90, soft_ratio=1.00,
        weight_compactness=args.weight_compactness,
        weight_adjacency=args.weight_adjacency,
        weight_factor_score=args.weight_factor,
        progress_fn=events.append,
    )
    total_s = round(time.time() - started, 1)

    manual = rule_errors(manual_plan(df_sev, farm, year), data, year)
    row = {
        "farm": farm,
        "status": info.get("status"),
        "fields_total": info.get("n_fields_total"),
        "fields_model": info.get("n_fields_model"),
        "target_cultures": len(targets),
        "micro_targets": len(micro_targets(targets)),
        "cultures_model": info.get("n_cultures_model"),
        "first_clean_s": None,
        "solve_s": info.get("solve_time"),
        "total_s": total_s,
        "solver_rotation_errors": info.get("rotation_errors"),
        "auto_errors": None,
        "manual_errors": sum(manual.values()),
        **{f"manual_{name}": manual.get(name, 0) for name in RULES.values()},
        "area_pct": None,
        "min_culture_pct": None,
    }
    if plan is None:
        return row

    clean = [e["elapsed"] for e in events
             if e.get("type") == "progress" and e.get("rotation_errors") == 0]
    auto = rule_errors(plan, data, year)
    area_pct, min_culture_pct = area_fulfilment(plan, targets)
    row.update(
        first_clean_s=min(clean) if clean else None,
        auto_errors=sum(auto.values()),
        area_pct=area_pct,
        min_culture_pct=min_culture_pct,
    )
    return row


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument("--farms", nargs="+", default=RAT_FARMS)
    parser.add_argument("--max-time", type=int, default=settings.solver_max_time)
    parser.add_argument("--weight-compactness", type=int, default=0)
    parser.add_argument("--weight-adjacency", type=int, default=2)
    parser.add_argument("--weight-factor", type=int, default=3)
    args = parser.parse_args()
    settings.solver_max_time = args.max_time

    print(f"Загрузка данных из {settings.data_dir} ...", flush=True)
    df_sev, safe_intervals, return_rules, compat_matrix = get_all_data()
    data = (without_season_chemistry(df_sev, args.year),
            safe_intervals, return_rules, compat_matrix)

    known = set(df_sev["farm"].dropna().astype(str))
    missing = [f for f in args.farms if f not in known]
    if missing:
        raise SystemExit(f"Нет в данных: {', '.join(missing)}")

    rows = []
    for farm in args.farms:
        print(f"→ {farm}", flush=True)
        row = run_farm(farm, args.year, data, args)
        rows.append(row)
        print(f"  {row}", flush=True)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / f"rat_backtest_{args.year}.csv"
    with out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    solved = [r for r in rows if r["auto_errors"] is not None]
    print(f"\nХозяйств: {len(rows)}, с планом: {len(solved)}")
    print(f"Нарушений: автоплан {sum(r['auto_errors'] for r in solved)}, "
          f"ручной план {sum(r['manual_errors'] for r in rows)}")
    print(f"Таблица: {out.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
