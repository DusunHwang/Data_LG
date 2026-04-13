"""내장 데이터셋 생성 스크립트 (seed=42 고정)"""

from pathlib import Path

import numpy as np
import pandas as pd

SEED = 42
OUTPUT_DIR = Path(__file__).parent
rng = np.random.default_rng(SEED)


def generate_manufacturing_regression() -> pd.DataFrame:
    """
    제조 공정 회귀 데이터셋
    - 12,000행 × 48열
    - 블록 결측 패턴 (장비 정지 시간대)
    - 저카디널리티 공정 그룹 컬럼
    - 타깃: 최종 제품 품질 점수 (quality_score)
    """
    np.random.seed(SEED)
    n = 12000

    # 공정 그룹 (저카디널리티)
    process_lines = [f"LINE_{i}" for i in range(1, 7)]  # 6개 라인
    shifts = ["A", "B", "C"]
    equipment_types = ["MACHINE_A", "MACHINE_B", "MACHINE_C", "MACHINE_D"]

    df = pd.DataFrame()
    df["process_line"] = np.random.choice(process_lines, n)
    df["shift"] = np.random.choice(shifts, n)
    df["equipment_type"] = np.random.choice(equipment_types, n)

    # 온도 센서 (8개)
    for i in range(1, 9):
        base_temp = 150 + i * 10
        df[f"temp_sensor_{i:02d}"] = np.random.normal(base_temp, 5, n)

    # 압력 센서 (6개)
    for i in range(1, 7):
        df[f"pressure_{i:02d}"] = np.random.normal(2.5 + i * 0.3, 0.2, n)

    # 유량 센서 (5개)
    for i in range(1, 6):
        df[f"flow_rate_{i:02d}"] = np.random.normal(100 + i * 5, 10, n)

    # 진동 센서 (4개)
    for i in range(1, 5):
        df[f"vibration_{i:02d}"] = np.abs(np.random.normal(0, 0.5 + i * 0.1, n))

    # 전력 소비 (4개)
    for i in range(1, 5):
        df[f"power_consumption_{i:02d}"] = np.random.normal(50 + i * 10, 5, n)

    # 원자재 품질 지표 (5개)
    for i in range(1, 6):
        df[f"raw_material_quality_{i:02d}"] = np.random.uniform(0.8, 1.0, n)

    # 공정 시간 (3개)
    for i in range(1, 4):
        df[f"process_time_{i:02d}"] = np.random.exponential(30 + i * 5, n)

    # 습도/환경
    df["ambient_humidity"] = np.random.normal(45, 8, n)
    df["ambient_temperature"] = np.random.normal(22, 3, n)

    # 타깃: 품질 점수 (여러 피처의 선형 조합 + 노이즈)
    quality_score = (
        0.3 * df["temp_sensor_01"] / 150
        + 0.2 * df["pressure_01"] / 2.5
        + 0.15 * df["raw_material_quality_01"]
        - 0.1 * df["vibration_01"]
        + np.random.normal(0, 0.05, n)
    )
    df["quality_score"] = (quality_score - quality_score.min()) / (quality_score.max() - quality_score.min()) * 100

    # 블록 결측 패턴 (장비 정지 시간대: 약 15% 블록)
    block_size = 50
    n_blocks = int(n * 0.15 / block_size)
    missing_cols = [f"temp_sensor_{i:02d}" for i in range(5, 9)] + \
                   [f"pressure_{i:02d}" for i in range(4, 7)] + \
                   [f"vibration_{i:02d}" for i in range(3, 5)]

    for _ in range(n_blocks):
        start = np.random.randint(0, n - block_size)
        for col in np.random.choice(missing_cols, size=len(missing_cols) // 2, replace=False):
            df.loc[start:start + block_size, col] = np.nan

    # 일부 수치형 결측값 (랜덤, ~5%)
    for col in ["ambient_humidity", "ambient_temperature"]:
        mask = np.random.random(n) < 0.05
        df.loc[mask, col] = np.nan

    print(f"manufacturing_regression: {df.shape}, 결측 {df.isna().sum().sum()} cells")
    return df


def generate_instrument_measurement() -> pd.DataFrame:
    """
    계측 장비 측정 데이터셋
    - 8,000행 × 40열
    - 장비 고유 결측 패턴
    - 타깃: 측정값 오차 (measurement_error)
    """
    np.random.seed(SEED + 1)
    n = 8000

    equipment_ids = [f"EQ_{i:03d}" for i in range(1, 21)]  # 20개 장비
    df = pd.DataFrame()
    df["equipment_id"] = np.random.choice(equipment_ids, n)
    df["measurement_type"] = np.random.choice(["TYPE_A", "TYPE_B", "TYPE_C"], n)
    df["calibration_status"] = np.random.choice(["CALIBRATED", "UNCALIBRATED", "EXPIRED"], n,
                                                   p=[0.7, 0.2, 0.1])

    # 측정 조건
    df["sample_temperature"] = np.random.normal(20, 5, n)
    df["sample_pressure"] = np.random.normal(101.3, 2, n)
    df["sample_humidity"] = np.random.normal(50, 10, n)
    df["measurement_duration"] = np.random.exponential(30, n)

    # 장비 센서값 (장비별로 다른 특성)
    for i in range(1, 13):
        noise_level = 0.1 * (i % 4 + 1)
        df[f"sensor_channel_{i:02d}"] = np.random.normal(5.0 + i * 0.5, noise_level, n)

    # 참조 표준값
    for i in range(1, 6):
        df[f"reference_standard_{i:02d}"] = np.random.normal(10.0 + i, 0.01, n)

    # 보정 계수
    for i in range(1, 5):
        df[f"correction_factor_{i:02d}"] = np.random.uniform(0.95, 1.05, n)

    # 환경 영향 인자
    for i in range(1, 7):
        df[f"env_factor_{i:02d}"] = np.random.normal(1.0, 0.02, n)

    # 타깃: 측정 오차
    measurement_error = (
        0.05 * df["sample_temperature"] / 20
        + 0.03 * df["sensor_channel_01"]
        - 0.04 * df["correction_factor_01"]
        + 0.02 * (df["calibration_status"] == "UNCALIBRATED").astype(float)
        + np.random.normal(0, 0.01, n)
    )
    df["measurement_error"] = measurement_error * 100

    # 장비별 결측 패턴
    eq_array = df["equipment_id"].values
    for eq_id in equipment_ids[:5]:  # 5개 장비는 특정 채널 결측
        mask = eq_array == eq_id
        missing_channels = [f"sensor_channel_{i:02d}" for i in range(9, 13)]
        for col in missing_channels:
            sub_mask = mask & (np.random.random(n) < 0.3)
            df.loc[sub_mask, col] = np.nan

    # 랜덤 결측 (~3%)
    for col in ["sample_humidity", "measurement_duration"]:
        mask = np.random.random(n) < 0.03
        df.loc[mask, col] = np.nan

    print(f"instrument_measurement: {df.shape}, 결측 {df.isna().sum().sum()} cells")
    return df


def generate_general_tabular_regression() -> pd.DataFrame:
    """
    일반 테이블형 회귀 데이터셋
    - 5,000행 × 30열
    - 혼합 수치/범주형 변수
    - ID형 컬럼 포함
    - 타깃: 연속형 점수 (target_score)
    """
    np.random.seed(SEED + 2)
    n = 5000

    df = pd.DataFrame()

    # ID형 컬럼 (회귀 타깃으로 부적합, 고유값 비율 높음)
    df["record_id"] = [f"REC_{i:06d}" for i in range(n)]
    df["batch_code"] = [f"BATCH_{np.random.randint(1, 501):04d}" for _ in range(n)]

    # 카테고리형 변수
    df["region"] = np.random.choice(["NORTH", "SOUTH", "EAST", "WEST", "CENTER"], n)
    df["product_category"] = np.random.choice([f"CAT_{i}" for i in range(1, 11)], n)
    df["customer_segment"] = np.random.choice(["PREMIUM", "STANDARD", "BASIC"], n, p=[0.2, 0.5, 0.3])
    df["season"] = np.random.choice(["SPRING", "SUMMER", "AUTUMN", "WINTER"], n)
    df["channel"] = np.random.choice(["ONLINE", "OFFLINE", "HYBRID"], n)

    # 수치형 변수
    df["age"] = np.random.randint(18, 80, n).astype(float)
    df["income"] = np.random.lognormal(10, 1, n)
    df["usage_frequency"] = np.random.exponential(5, n)
    df["satisfaction_score"] = np.random.uniform(1, 10, n)
    df["complaint_count"] = np.random.poisson(1.5, n).astype(float)
    df["tenure_months"] = np.random.randint(1, 120, n).astype(float)

    # 파생 피처
    for i in range(1, 9):
        df[f"feature_{i:02d}"] = np.random.normal(0, 1, n)

    # 비율 피처
    for i in range(1, 5):
        df[f"ratio_{i:02d}"] = np.random.beta(2, 5, n)

    # 타깃
    customer_segment_map = {"PREMIUM": 1.5, "STANDARD": 1.0, "BASIC": 0.5}
    segment_effect = df["customer_segment"].map(customer_segment_map).values

    target_score = (
        0.3 * segment_effect
        + 0.2 * df["satisfaction_score"] / 10
        + 0.1 * df["feature_01"]
        + 0.15 * df["ratio_01"]
        - 0.1 * df["complaint_count"] / 5
        + np.random.normal(0, 0.1, n)
    )
    df["target_score"] = (target_score - target_score.min()) / (target_score.max() - target_score.min()) * 100

    # 결측값 (~5-8%)
    for col in ["age", "income", "satisfaction_score", "feature_03", "feature_07"]:
        mask = np.random.random(n) < 0.07
        df.loc[mask, col] = np.nan

    for col in ["complaint_count", "ratio_02", "ratio_04"]:
        mask = np.random.random(n) < 0.05
        df.loc[mask, col] = np.nan

    print(f"general_tabular_regression: {df.shape}, 결측 {df.isna().sum().sum()} cells")
    return df


def generate_large_sampling_regression() -> pd.DataFrame:
    """
    대용량 샘플링 테스트 데이터셋
    - 250,000행 × 25열
    - 플롯 샘플링 기능 테스트용
    - 타깃: 연속형 (target_value)
    """
    np.random.seed(SEED + 3)
    n = 250000

    df = pd.DataFrame()

    # 시간 관련
    df["timestamp_idx"] = np.arange(n)
    df["hour_of_day"] = np.arange(n) % 24
    df["day_of_week"] = (np.arange(n) // 24) % 7

    # 센서 데이터 (연속형)
    for i in range(1, 11):
        freq = 0.01 * i
        df[f"sensor_{i:02d}"] = (
            np.sin(np.arange(n) * freq) * 10
            + np.random.normal(0, 1, n)
        )

    # 카테고리형
    df["zone"] = np.random.choice(["A", "B", "C", "D"], n)
    df["status"] = np.random.choice(["NORMAL", "WARNING", "CRITICAL"], n, p=[0.8, 0.15, 0.05])

    # 파생 피처
    df["rolling_mean_5"] = pd.Series(df["sensor_01"]).rolling(5, min_periods=1).mean().values
    df["rolling_std_5"] = pd.Series(df["sensor_01"]).rolling(5, min_periods=1).std().fillna(0).values
    df["lag_1"] = pd.Series(df["sensor_01"]).shift(1).fillna(0).values
    df["lag_24"] = pd.Series(df["sensor_01"]).shift(24).fillna(0).values

    # 타깃
    target_value = (
        0.4 * df["sensor_01"]
        + 0.2 * df["sensor_02"]
        - 0.1 * df["sensor_03"]
        + 0.15 * df["rolling_mean_5"]
        + np.random.normal(0, 2, n)
    )
    df["target_value"] = target_value

    # 결측값 최소화 (~1%)
    for col in ["sensor_05", "sensor_08"]:
        mask = np.random.random(n) < 0.01
        df.loc[mask, col] = np.nan

    print(f"large_sampling_regression: {df.shape}, 결측 {df.isna().sum().sum()} cells")
    return df


def generate_wide_missingness_stress() -> pd.DataFrame:
    """
    Wide table UI/결측 처리 스트레스 테스트 데이터셋
    - 5,000행 × 800열
    - 560개 컬럼: 약 80% 결측
    - 160개 컬럼: 약 70% 결측
    - 80개 컬럼: 결측 없음 (타깃 wide_target 포함)
    """
    np.random.seed(SEED + 4)
    n = 5000
    n_high_missing = 560
    n_medium_missing = 160
    n_complete_features = 79

    data: dict[str, np.ndarray] = {}

    for i in range(1, n_high_missing + 1):
        values = np.random.normal(loc=0.0, scale=1.0, size=n)
        mask = np.random.random(n) < 0.80
        values[mask] = np.nan
        data[f"high_missing_feature_{i:03d}"] = values

    for i in range(1, n_medium_missing + 1):
        values = np.random.normal(loc=5.0, scale=2.0, size=n)
        mask = np.random.random(n) < 0.70
        values[mask] = np.nan
        data[f"medium_missing_feature_{i:03d}"] = values

    complete_matrix = np.random.normal(loc=10.0, scale=3.0, size=(n, n_complete_features))
    for i in range(1, n_complete_features + 1):
        data[f"complete_feature_{i:03d}"] = complete_matrix[:, i - 1]

    target = (
        0.35 * complete_matrix[:, 0]
        - 0.20 * complete_matrix[:, 1]
        + 0.15 * complete_matrix[:, 2]
        + 0.10 * np.sin(complete_matrix[:, 3])
        + np.random.normal(0, 0.5, n)
    )
    data["wide_target"] = target

    df = pd.DataFrame(data)
    print(f"wide_missingness_stress: {df.shape}, 결측 {df.isna().sum().sum()} cells")
    return df


def main():
    """데이터셋 생성 및 저장"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    generators = {
        "manufacturing_regression": generate_manufacturing_regression,
        "instrument_measurement": generate_instrument_measurement,
        "general_tabular_regression": generate_general_tabular_regression,
        "large_sampling_regression": generate_large_sampling_regression,
        "wide_missingness_stress": generate_wide_missingness_stress,
    }

    for name, generator_func in generators.items():
        print(f"\n[{name}] 생성 중...")
        df = generator_func()
        output_path = OUTPUT_DIR / f"{name}.parquet"
        df.to_parquet(output_path, index=False, engine="pyarrow")
        print(f"  -> 저장 완료: {output_path} ({output_path.stat().st_size / 1024 / 1024:.1f} MB)")

    print("\n=== 모든 데이터셋 생성 완료 ===")
    for name in generators:
        path = OUTPUT_DIR / f"{name}.parquet"
        df = pd.read_parquet(path)
        print(f"  {name}: {df.shape[0]:,}행 × {df.shape[1]}열, {path.stat().st_size / 1024 / 1024:.1f}MB")


if __name__ == "__main__":
    main()
