"""Окружение, подмена тяжёлых загрузчиков, фикстуры.

sys.path не трогаем: `pythonpath = backend` из pytest.ini применяется раньше,
чем импортируется этот файл.
"""
import os
import tempfile
from pathlib import Path

import pytest

# --- 1) env ДО первого импорта app.config (Settings() исполняется на импорте) ---
_TMP = Path(tempfile.mkdtemp(prefix="soil-planner-tests-"))
os.environ["LOGS_DIR"] = str(_TMP / "logs")
os.environ["RESULTS_DIR"] = str(_TMP / "results")
os.environ["AUTH_ENABLED"] = "true"
os.environ["APP_USERNAME"] = "tester"
os.environ["APP_PASSWORD"] = "secret"
os.environ["JWT_SECRET"] = "test-secret-long-enough-0123456789abcdef"
os.environ["SOLVER_MAX_TIME"] = "5"
os.environ["SOLVER_WORKERS"] = "1"

from synthetic import (  # noqa: E402
    build_compat_matrix, build_crop_targets, build_df_sev,
    build_return_rules, build_safe_intervals, write_mini_registry,
)

# --- 2) подмена загрузчиков ДО импорта app.main -------------------------------
# app/main.py на импорте стартует daemon-поток _warmup_data → get_all_data()
# → openpyxl на 11 МБ (~50 c). Реальный Excel в тестах не открывается никогда.
import app.core.data_loader as _dl  # noqa: E402

_FAKE_DATA = (build_df_sev(), build_safe_intervals(),
              build_return_rules(), build_compat_matrix())
_dl.get_all_data = lambda: _FAKE_DATA
_dl.load_sevooborot_with_chem = lambda: _FAKE_DATA[0]
_dl.load_field_geometries = lambda: {}

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "backend" / "data"


# --- 3) данные ----------------------------------------------------------------
@pytest.fixture
def df_sev():
    return build_df_sev()


@pytest.fixture
def return_rules():
    return build_return_rules()


@pytest.fixture
def compat_matrix():
    return build_compat_matrix()


@pytest.fixture
def safe_intervals():
    return build_safe_intervals()


@pytest.fixture
def crop_targets():
    return build_crop_targets()


@pytest.fixture
def registry():
    """Настоящий реестр из backend/data — без кэша, чтобы тесты не влияли друг на друга."""
    from app.core.factors.registry import load_registry
    return load_registry(data_dir=DATA_DIR)


@pytest.fixture
def mini_registry(tmp_path):
    from app.core.factors.registry import load_registry
    return load_registry(data_dir=write_mini_registry(tmp_path))


@pytest.fixture
def taxonomy():
    """Справочник культур из backend/data/crop_colors.csv."""
    from app.core.crops import CropTaxonomy, load_crop_reference
    return CropTaxonomy(load_crop_reference(str(DATA_DIR / "crop_colors.csv")))


@pytest.fixture
def scorer(df_sev, compat_matrix, registry, taxonomy):
    """FactorScorer на синтетике: реестр настоящий, дополнительных данных нет."""
    from app.core.factors import FactorScorer
    from app.core.factors.inputs import FactorInputs
    return FactorScorer(
        planning_year=2027, df_sev=df_sev, compat_matrix=compat_matrix,
        registry=registry, taxonomy=taxonomy, inputs=FactorInputs(),
    )


@pytest.fixture
def solve(df_sev, return_rules, compat_matrix, safe_intervals):
    """Быстрый прогон солвера: solve(targets=..., planning_year=2027, **kw)."""
    from app.core.solver import optimize_rotation_with_chemicals

    def _solve(targets=None, planning_year=2027, **kw):
        frame = kw.pop("df", df_sev)
        params = dict(
            planning_year=planning_year,
            crop_targets=targets if targets is not None else build_crop_targets(),
            hard_ratio=0.90, soft_ratio=1.00,
            max_time=5,      # синтетика решается за миллисекунды
            num_workers=1,   # детерминированнее, без разогрева пула
            weight_compactness=0,
            weight_factor_score=0,
        )
        params.update(kw)
        return optimize_rotation_with_chemicals(
            frame, return_rules, compat_matrix, safe_intervals, **params
        )

    return _solve


# --- 4) HTTP ------------------------------------------------------------------
@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def auth(client):
    r = client.post("/api/auth/login",
                    data={"username": "tester", "password": "secret"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}
