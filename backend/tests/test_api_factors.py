"""HTTP-контракты подсистемы факторов СПП и палитры культур."""
import json

import pytest

from synthetic import PEA, WHEAT


def test_endpoints_require_auth(client):
    for path in ("/api/factors", "/api/factors/coverage", "/api/data/crop-colors"):
        assert client.get(path).status_code == 401, path


def test_factors_registry_endpoint(client, auth):
    response = client.get("/api/factors", headers=auth)
    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 24
    assert payload["total_weight"] == 100.0

    by_id = {f["id"]: f for f in payload["factors"]}
    ph = by_id["soil_ph"]
    assert ph["weight"] == 8.0
    assert len(ph["bands"]) == 7
    # бесконечные границы уезжают в JSON как null, а не как Infinity
    assert ph["bands"][0]["lo"] is None
    assert ph["bands"][-1]["hi"] is None
    assert "Infinity" not in response.text

    texture = by_id["soil_texture"]
    assert any(v["value"] == "глина" for v in texture["enum_values"])
    assert by_id["rotation_history"]["role"] == "hard_blocker"


def test_factors_coverage_endpoint(client, auth):
    response = client.get("/api/factors/coverage?planning_year=2027", headers=auth)
    assert response.status_code == 200
    payload = response.json()
    assert payload["scoring_weight"] == 91.0
    assert payload["constraint_weight"] == 9.0
    assert 0.0 <= payload["coverage"] <= 1.0
    statuses = {f["id"]: f["status"] for f in payload["factors"]}
    assert statuses["predecessor_good"] == "available"
    assert statuses["soil_ph"] == "no_data"


def test_explain_endpoint(client, auth):
    response = client.get(
        f"/api/factors/explain?field_id=A-01&culture={PEA}&planning_year=2027",
        headers=auth,
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["field_id"] == "A-01"
    assert payload["predecessor"] == WHEAT
    assert payload["tillage"] == "классическая с плугом"
    assert payload["residue_multiplier"] == 1.0
    assert 0 <= payload["score"] <= 100
    factors = {f["id"]: f for f in payload["factors"]}
    assert factors["predecessor_good"]["available"] is True
    assert factors["soil_ph"]["available"] is False
    assert "field_soil.csv" in factors["soil_ph"]["reason"]


def test_explain_unknown_field_is_404(client, auth):
    response = client.get(
        "/api/factors/explain?field_id=нет-такого&culture=Горох", headers=auth
    )
    assert response.status_code == 404


def test_score_endpoint_ranks_cultures(client, auth):
    response = client.get(
        f"/api/factors/score?field_id=A-01&planning_year=2027"
        f"&cultures={WHEAT}&cultures={PEA}",
        headers=auth,
    )
    assert response.status_code == 200
    cultures = response.json()["cultures"]
    assert [c["culture"] for c in cultures] == [PEA, WHEAT]  # горох после пшеницы лучше
    assert cultures[0]["score"] >= cultures[1]["score"]


def test_crop_colors_endpoint(client, auth):
    response = client.get("/api/data/crop-colors", headers=auth)
    assert response.status_code == 200
    payload = response.json()
    assert payload["reference_size"] == 193
    colors = payload["colors"]
    assert colors[WHEAT]["color"] == "#F5B413"
    assert colors[WHEAT]["matched"] is True
    assert colors[WHEAT]["category"] == "Зерновые"
    # нецелевые категории тоже должны быть раскрашены
    assert colors["Пар"]["matched"] is True
    for entry in colors.values():
        assert entry["color"].startswith("#")


def test_optimization_run_reports_factor_score(client, auth):
    body = {
        "crop_targets": {"Ферма-Б": {WHEAT: 100.0, PEA: 50.0}},
        "planning_year": 2027, "hard_ratio": 0.9, "soft_ratio": 1.0,
        "weight_compactness": 0, "weight_factor_score": 3,
        "run_id": "pytest-factors",
    }
    events = []
    with client.stream("POST", "/api/optimization/run", json=body, headers=auth) as r:
        assert r.status_code == 200
        for line in r.iter_lines():
            if not line.startswith("data: "):
                continue                       # ": keepalive"
            payload = line[6:]
            if payload == "[DONE]":
                continue
            events.append(json.loads(payload))

    assert events[-1]["type"] == "result"
    data = events[-1]["data"]
    assert data["success"] is True

    info = data["result_info"]
    assert info["factor_weight"] == 3
    assert info["factor_score_avg"] is not None
    assert info["factor_score_area_avg"] is not None

    coverage = data["factors_coverage"]
    assert coverage["scoring_weight"] == 91.0
    assert 0.0 <= coverage["coverage"] <= 1.0

    assert all("factor_score" in row for row in data["optimized_plan"])


@pytest.mark.parametrize("weight", [-1, 51])
def test_factor_weight_is_bounded(client, auth, weight):
    body = {"crop_targets": {"Ферма-Б": {WHEAT: 100.0}}, "planning_year": 2027,
            "weight_factor_score": weight}
    assert client.post("/api/optimization/run", json=body,
                       headers=auth).status_code == 422
