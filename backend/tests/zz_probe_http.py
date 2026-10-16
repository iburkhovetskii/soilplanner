"""Проверки HTTP-слоя: краевые случаи, NaN в JSON, обратная совместимость."""
import json
import shutil
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def _sse(client, auth, payload):
    with client.stream("POST", "/api/optimization/run", json=payload,
                       headers=auth) as r:
        assert r.status_code == 200, r.text
        raw = "".join(chunk for chunk in r.iter_text())
    result, strict_fail = None, []
    for block in raw.split("\n\n"):
        block = block.strip()
        if not block.startswith("data: "):
            continue
        body = block[len("data: "):]
        if body == "[DONE]":
            continue
        try:
            json.loads(body, parse_constant=_reject)   # как JSON.parse в браузере
        except Exception as e:
            strict_fail.append((type(e).__name__, str(e)))
        try:
            ev = json.loads(body)   # питоновский json — NaN пропускает
        except json.JSONDecodeError:
            continue
        if ev.get("type") == "result":
            result = ev["data"]
        if ev.get("type") == "error":
            print("!!! SSE ERROR:", ev)
    return raw, result, strict_fail


def _reject(name):
    raise ValueError(f"invalid JSON constant {name}")


BASE = {"planning_year": 2027, "hard_ratio": 0.90, "soft_ratio": 1.00,
        "weight_compactness": 0}


def test_a_run_nan(client, auth):
    raw, result, strict_fail = _sse(client, auth, {**BASE, "run_id": "probe001"})
    print("STRICT-JSON FAILURES:", strict_fail)
    print("raw содержит 'NaN':", "NaN" in raw)
    assert result is not None


def test_b_last_result(client, auth):
    r = client.get("/api/optimization/last-result", headers=auth)
    print("last-result:", r.status_code)


def test_c_saved_new(client, auth):
    r = client.get("/api/optimization/results?scope=all", headers=auth)
    metas = r.json()
    print("saved:", [m["run_id"] for m in metas])
    for m in metas:
        rr = client.get(f"/api/optimization/results/{m['run_id']}", headers=auth)
        print(f"  GET {m['run_id']} -> {rr.status_code}")


def test_d_old_saved_result(client, auth):
    """Старые файлы результатов из backend/results — открываются ли."""
    from app.config import settings
    settings.results_dir.mkdir(parents=True, exist_ok=True)
    for src in (REPO / "backend" / "results").glob("*.json"):
        shutil.copy(src, settings.results_dir / src.name)
    r = client.get("/api/optimization/results?scope=all", headers=auth)
    print("LIST старых:", json.dumps(r.json(), ensure_ascii=False)[:1500])
    for m in r.json():
        rr = client.get(f"/api/optimization/results/{m['run_id']}", headers=auth)
        print(f"  GET {m['run_id']} -> {rr.status_code}")
        if rr.status_code == 200:
            data = rr.json()
            plan = data["result"].get("optimized_plan") or []
            if plan:
                print("    row0 keys:", sorted(plan[0].keys()))


def test_e_factors_registry(client, auth):
    r = client.get("/api/factors", headers=auth)
    print("/api/factors:", r.status_code, "len", len(r.content))
    d = r.json()
    print("  count/total_weight:", d["count"], d["total_weight"])


@pytest.mark.parametrize("params", [
    {"field_id": "НЕТ-ТАКОГО", "culture": "Горох"},
    {"field_id": "A-01", "culture": ""},
    {"field_id": "A-01", "culture": "Горох", "planning_year": 1900},
    {"field_id": "A-01", "culture": "Горох", "planning_year": 99999},
    {"field_id": "A-01", "culture": "Горох", "planning_year": -5},
    {"field_id": "A-06", "culture": "Горох"},   # многолетнее поле
])
def test_f_explain_edges(client, auth, params):
    r = client.get("/api/factors/explain", params=params, headers=auth)
    print("explain", params, "->", r.status_code, r.text[:250])


@pytest.mark.parametrize("qs", [
    "field_id=A-01",
    "field_id=A-01&cultures=",
    "field_id=A-01&cultures=Горох&cultures=Нет+такой+культуры",
    "field_id=НЕТ",
    "field_id=A-01&planning_year=1900",
])
def test_g_score_edges(client, auth, qs):
    r = client.get(f"/api/factors/score?{qs}", headers=auth)
    print("score", qs, "->", r.status_code, r.text[:300])


def test_h_score_big_list(client, auth):
    from app.core.data_loader import load_cultures_reference
    ref = load_cultures_reference()
    cultures = [str(c) for c in ref["culture"].dropna().astype(str)]
    print("справочник культур:", len(cultures))
    qs = "&".join(["field_id=A-01"] + [f"cultures={c}" for c in cultures[:2000]])
    t = time.time()
    r = client.get(f"/api/factors/score?{qs}", headers=auth)
    print("big score ->", r.status_code, f"{time.time()-t:.2f}s", len(r.content))


def test_i_coverage(client, auth):
    for y in (2027, 1900, 99999):
        r = client.get(f"/api/factors/coverage?planning_year={y}", headers=auth)
        print("coverage", y, "->", r.status_code, len(r.content))


def test_j_crop_colors(client, auth):
    for q in ("", "?year=2026", "?year=1800"):
        t = time.time()
        r = client.get(f"/api/data/crop-colors{q}", headers=auth)
        print(f"crop-colors{q} -> {r.status_code}, {len(r.content)} байт, "
              f"{time.time()-t:.2f}s")
        if r.status_code == 200:
            d = r.json()
            print("   matched/total/reference:", d["matched"], d["total"],
                  d["reference_size"])


def test_k_export_with_new_columns(client, auth):
    from app.core import run_store
    data = run_store.load_result("probe001")
    assert data, "нет сохранённого прогона probe001"
    plan = data["result"]["optimized_plan"]
    body = {"optimized_plan": plan,
            "crop_targets": data["result"]["crop_targets"]}
    for ep in ("/api/export/csv", "/api/export/excel"):
        r = client.post(ep, json=body, headers=auth)
        print(ep, "->", r.status_code, len(r.content))
        if r.status_code != 200:
            print("   ", r.text[:500])
    r = client.post("/api/export/csv", json=body, headers=auth)
    if r.status_code == 200:
        print("CSV head:\n", r.content.decode("utf-8-sig")[:600])
