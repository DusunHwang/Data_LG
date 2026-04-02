"""애플리케이션 설정 모듈"""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """환경 변수 기반 설정 클래스"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # PostgreSQL 설정
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "regression_platform"
    postgres_user: str = "app"
    postgres_password: str = "changeme"

    @property
    def database_url(self) -> str:
        """비동기 DB URL"""
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def sync_database_url(self) -> str:
        """동기 DB URL (Alembic용)"""
        return (
            f"postgresql+psycopg2://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    # Redis 설정
    redis_host: str = "localhost"
    redis_port: int = 6379

    @property
    def redis_url(self) -> str:
        """Redis URL"""
        return f"redis://{self.redis_host}:{self.redis_port}/0"

    # JWT 설정
    secret_key: str = "your-secret-key-change-in-production"
    access_token_expire_minutes: int = 60
    refresh_token_expire_days: int = 7
    algorithm: str = "HS256"

    # vLLM 설정
    vllm_endpoint_small: str = "http://dusun.iptime.org:27800/v1"
    vllm_model_small: str = "Qwen/Qwen3-14B-FP8"
    vllm_temperature: float = 0.1
    vllm_max_tokens: int = 4000

    # 아티팩트 저장소 설정
    artifact_store_root: str = "/data/app/artifacts"
    builtin_dataset_path: str = "/app/datasets_builtin"

    # 앱 설정
    app_env: Literal["development", "production", "test"] = "development"
    log_level: str = "INFO"
    max_upload_mb: int = 100
    max_shap_rows: int = 5000
    plot_sampling_threshold_rows: int = 200000
    default_session_ttl_days: int = 7
    default_subset_limit: int = 5
    job_timeout_seconds: int = 600

    @property
    def max_upload_bytes(self) -> int:
        """최대 업로드 크기 (바이트)"""
        return self.max_upload_mb * 1024 * 1024

    @property
    def is_development(self) -> bool:
        """개발 환경 여부"""
        return self.app_env == "development"


@lru_cache()
def get_settings() -> Settings:
    """설정 싱글톤 반환"""
    return Settings()


# 전역 설정 인스턴스
settings = get_settings()
