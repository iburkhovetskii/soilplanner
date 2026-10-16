"""
FastAPI application entry point.
"""
import logging
from logging.handlers import RotatingFileHandler

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path

from app.config import settings
from app.auth.router import router as auth_router
from app.data.router import router as data_router
from app.optimization.router import router as optimization_router
from app.export.router import router as export_router
from app.factors.router import router as factors_router


# Папки логов и результатов (могут быть смонтированы как volume)
settings.logs_dir.mkdir(parents=True, exist_ok=True)
settings.results_dir.mkdir(parents=True, exist_ok=True)

# Логи пишем и в консоль, и в файл logs/app.log (с ротацией)
_file_handler = RotatingFileHandler(
    settings.logs_dir / "app.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8"
)
_file_handler.setFormatter(
    logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
)
_root = logging.getLogger()
if _root.level > logging.INFO or _root.level == logging.NOTSET:
    _root.setLevel(logging.INFO)
if not any(isinstance(h, RotatingFileHandler) for h in _root.handlers):
    _root.addHandler(_file_handler)

# Защита: при включённой авторизации не позволяем работать с известным/слабым
# JWT-секретом (он лежит в репозитории — иначе токены подделываются). Если
# секрет не задан в окружении, генерируем временный на время жизни процесса.
_INSECURE_JWT_SECRETS = {
    "change-me-in-production-use-a-long-random-string",
    "dev-secret-not-for-production",
    "local-dev-secret-change-me-in-prod",
}
if settings.auth_enabled and (
    settings.jwt_secret in _INSECURE_JWT_SECRETS or len(settings.jwt_secret) < 16
):
    import secrets as _secrets
    settings.jwt_secret = _secrets.token_urlsafe(48)
    logging.getLogger("soil_planner").warning(
        "JWT_SECRET не задан или небезопасен — сгенерирован временный секрет на "
        "время работы процесса. Задайте JWT_SECRET в окружении, иначе все токены "
        "инвалидируются при каждом рестарте."
    )

app = FastAPI(
    title="Soil Planner API",
    description="API для оптимизации севооборота с учетом химобработки",
    version="2.0.0",
)

# CORS — в production фронт раздаётся из того же origin,
# но на всякий случай разрешаем всё
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(auth_router)
app.include_router(data_router)
app.include_router(optimization_router)
app.include_router(export_router)
app.include_router(factors_router)


# Прогрев данных в фоне при старте: холодная загрузка Excel/parquet занимает
# ~50с (openpyxl на 11 МБ файле). Без прогрева эту задержку платит первый
# запрос. Грузим в демон-потоке, чтобы сервер сразу принимал соединения.
def _warmup_data() -> None:
    import time
    log = logging.getLogger("soil_planner")
    try:
        from app.core.data_loader import get_all_data, load_field_geometries
        from app.core.crops import get_taxonomy
        from app.core.factors import get_inputs, get_registry
        t = time.time()
        get_all_data()
        load_field_geometries()
        get_taxonomy()
        get_registry()
        get_inputs()
        log.info("Данные прогреты за %.1fс", time.time() - t)
    except Exception:
        log.exception("Сбой прогрева данных")


import threading as _threading
_threading.Thread(target=_warmup_data, name="warmup", daemon=True).start()


@app.get("/api/health")
async def health():
    return {"status": "ok"}


# Serve frontend static files in production (single Docker image)
FRONTEND_DIST = Path(__file__).parent.parent / "static"
if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIST / "assets")), name="assets")

    @app.get("/{full_path:path}")
    async def serve_spa(request: Request, full_path: str):
        """SPA fallback: serve index.html for all non-API routes."""
        file_path = FRONTEND_DIST / full_path
        if file_path.is_file():
            return FileResponse(file_path)
        return FileResponse(FRONTEND_DIST / "index.html")
