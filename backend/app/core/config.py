"""애플리케이션 설정 모듈 (SQLite + 파일시스템 기반)"""

import os
from functools import lru_cache
from typing import Literal

# Keep numerical libraries from automatically using every core on large servers.
for _thread_env in (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
):
    os.environ.setdefault(_thread_env, "8")

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # SQLite 데이터베이스
    database_path: str = "./data/app.db"

    @property
    def database_url(self) -> str:
        """비동기 SQLite URL"""
        return f"sqlite+aiosqlite:///{self.database_path}"

    @property
    def sync_database_url(self) -> str:
        """동기 SQLite URL (Alembic, 워커용)"""
        return f"sqlite:///{self.database_path}"

    # JWT
    secret_key: str = "your-secret-key-change-in-production"
    access_token_expire_minutes: int = 60
    refresh_token_expire_days: int = 7
    algorithm: str = "HS256"

    # vLLM
    vllm_endpoint_small: str = "http://your-vllm-server/v1"
    vllm_model_small: str = "Qwen/Qwen3-30B-A3B-FP8"
    vllm_temperature: float = 0.1
    vllm_max_tokens: int = 4096

    # 아티팩트 / 데이터셋 경로
    artifact_store_root: str = "./data/artifacts"
    builtin_dataset_path: str = "./datasets_builtin"

    # 앱 설정
    app_env: Literal["development", "production", "test"] = "development"
    log_level: str = "INFO"
    max_upload_mb: int = 100
    max_shap_rows: int = 5000
    plot_sampling_threshold_rows: int = 200000
    default_session_ttl_days: int = 7
    default_subset_limit: int = 5
    job_timeout_seconds: int = 600
    compute_threads: int = 8
    worker_max_workers: int = 1

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def is_development(self) -> bool:
        return self.app_env == "development"

    def ensure_dirs(self):
        """필요한 디렉토리 생성"""
        os.makedirs(os.path.dirname(os.path.abspath(self.database_path)), exist_ok=True)
        os.makedirs(self.artifact_store_root, exist_ok=True)


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
