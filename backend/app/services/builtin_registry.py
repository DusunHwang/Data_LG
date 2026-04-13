"""내장 데이터셋 레지스트리"""

from pathlib import Path

import pandas as pd

from app.core.config import settings
from app.core.logging import get_logger
from app.schemas.dataset import BuiltinDatasetInfo

logger = get_logger(__name__)

# 내장 데이터셋 메타데이터
BUILTIN_DATASETS: dict[str, BuiltinDatasetInfo] = {
    "manufacturing_regression": BuiltinDatasetInfo(
        key="manufacturing_regression",
        name="제조 공정 회귀 데이터",
        description="반도체/제조 공정 데이터. 블록 결측 패턴, 저카디널리티 공정 그룹 포함. 12,000행 × 48열.",
        row_count=12000,
        col_count=48,
        tags=["제조", "공정", "회귀", "결측값"],
    ),
    "instrument_measurement": BuiltinDatasetInfo(
        key="instrument_measurement",
        name="계측 장비 측정 데이터",
        description="계측 장비별 측정 데이터. 장비 고유 결측 패턴 포함. 8,000행 × 40열.",
        row_count=8000,
        col_count=40,
        tags=["계측", "측정", "회귀", "장비"],
    ),
    "general_tabular_regression": BuiltinDatasetInfo(
        key="general_tabular_regression",
        name="일반 테이블형 회귀 데이터",
        description="혼합 수치/범주형 변수. ID형 컬럼 포함. 5,000행 × 30열.",
        row_count=5000,
        col_count=30,
        tags=["범용", "혼합형", "회귀", "범주형"],
    ),
    "large_sampling_regression": BuiltinDatasetInfo(
        key="large_sampling_regression",
        name="대용량 샘플링 테스트 데이터",
        description="플롯 샘플링 기능 테스트용 대용량 데이터. 250,000행 × 25열.",
        row_count=250000,
        col_count=25,
        tags=["대용량", "샘플링", "회귀"],
    ),
    "mpea_alloy": BuiltinDatasetInfo(
        key="mpea_alloy",
        name="MPEA 합금 물성 데이터",
        description="다주원소 합금 조성, 공정, 구조 및 기계적 물성 데이터. 724행 × 27열.",
        row_count=724,
        col_count=27,
        tags=["합금", "MPEA", "물성", "분류", "회귀"],
    ),
}

BUILTIN_DATASET_FILES: dict[str, str] = {
    "mpea_alloy": "mpea_alloy.csv",
}


def get_builtin_dataset_info(key: str) -> BuiltinDatasetInfo | None:
    """내장 데이터셋 정보 반환"""
    return BUILTIN_DATASETS.get(key)


def list_builtin_datasets() -> list[BuiltinDatasetInfo]:
    """내장 데이터셋 목록 반환"""
    return list(BUILTIN_DATASETS.values())


def get_builtin_dataset_path(key: str) -> Path:
    """내장 데이터셋 파일 경로 반환"""
    base_path = Path(settings.builtin_dataset_path)
    return base_path / BUILTIN_DATASET_FILES.get(key, f"{key}.parquet")


def load_builtin_dataset(key: str) -> pd.DataFrame:
    """내장 데이터셋 로드"""
    if key not in BUILTIN_DATASETS:
        raise ValueError(f"내장 데이터셋을 찾을 수 없습니다: {key}")

    file_path = get_builtin_dataset_path(key)
    if not file_path.exists():
        raise FileNotFoundError(
            f"내장 데이터셋 파일이 없습니다: {file_path}. "
            "먼저 'make generate-datasets'를 실행하세요."
        )

    logger.info("내장 데이터셋 로드", key=key, path=str(file_path))
    if file_path.suffix.lower() == ".csv":
        return pd.read_csv(file_path)
    return pd.read_parquet(file_path)


def builtin_dataset_exists(key: str) -> bool:
    """내장 데이터셋 파일 존재 여부 확인"""
    if key not in BUILTIN_DATASETS:
        return False
    return get_builtin_dataset_path(key).exists()
