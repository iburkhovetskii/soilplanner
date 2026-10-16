from pydantic_settings import BaseSettings
from pathlib import Path


class Settings(BaseSettings):
    # Auth — включена по умолчанию (secure by default). Одна учётка:
    # app_username / app_password (задаются через env APP_USERNAME / APP_PASSWORD).
    auth_enabled: bool = True
    app_username: str = "admin"
    app_password: str = "admin"
    jwt_secret: str = "change-me-in-production-use-a-long-random-string"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 1440  # 24 hours

    # Data
    data_dir: Path = Path(__file__).parent.parent / "data"

    # Logs & results (могут монтироваться как volume; переопределяются env
    # LOGS_DIR / RESULTS_DIR)
    logs_dir: Path = Path(__file__).parent.parent / "logs"
    results_dir: Path = Path(__file__).parent.parent / "results"

    # Solver
    solver_max_time: int = 120
    solver_workers: int = 8

    # CORS
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:3000"]

    model_config = {"env_prefix": "", "env_file": ".env", "extra": "ignore"}


settings = Settings()
