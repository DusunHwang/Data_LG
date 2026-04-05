"""LightGBM 모델링 유닛 테스트"""

import numpy as np
import pandas as pd


def make_regression_df(n=300, seed=42):
    """테스트용 회귀 데이터프레임"""
    rng = np.random.default_rng(seed)
    x1 = rng.normal(0, 1, n)
    x2 = rng.normal(0, 1, n)
    x3 = rng.choice(["A", "B", "C"], n)
    noise = rng.normal(0, 0.1, n)
    y = 2 * x1 - 0.5 * x2 + noise
    # 결측 주입
    mask1 = rng.random(n) < 0.1
    x1_missing = x1.copy().astype(float)
    x1_missing[mask1] = np.nan
    return pd.DataFrame({
        "feature_1": x1_missing,
        "feature_2": x2,
        "category": x3,
        "target": y,
    })


class TestFeatureMatrixBuilder:
    """Feature Matrix 구성 테스트"""

    def test_build_feature_matrix(self):
        """기본 feature matrix 구성"""
        from app.graph.subgraphs.modeling import build_feature_matrix

        df = make_regression_df()
        x, feature_cols = build_feature_matrix(df, target_col="target")

        assert x is not None
        assert len(feature_cols) > 0
        assert "target" not in feature_cols

    def test_constant_cols_excluded(self):
        """상수 컬럼 제거 확인"""
        from app.graph.subgraphs.modeling import build_feature_matrix

        df = make_regression_df()
        df["constant_col"] = 5.0  # 상수 컬럼 추가
        x, feature_cols = build_feature_matrix(df, target_col="target")

        assert "constant_col" not in feature_cols

    def test_target_not_in_features(self):
        """target이 feature에 포함되지 않음"""
        from app.graph.subgraphs.modeling import build_feature_matrix

        df = make_regression_df()
        _, feature_cols = build_feature_matrix(df, target_col="target")
        assert "target" not in feature_cols


class TestLightGBMTraining:
    """LightGBM 학습 테스트"""

    def test_train_lightgbm_basic(self):
        """LightGBM 기본 학습"""
        import lightgbm as lgb
        import numpy as np
        from sklearn.metrics import mean_squared_error, r2_score
        from sklearn.model_selection import train_test_split

        df = make_regression_df(n=500)
        # 범주형 인코딩
        df["category"] = df["category"].astype("category")

        x = df.drop("target", axis=1)
        y = df["target"]

        x_train, x_val, y_train, y_val = train_test_split(x, y, test_size=0.2, random_state=42)

        train_data = lgb.Dataset(x_train, label=y_train)
        val_data = lgb.Dataset(x_val, label=y_val, reference=train_data)

        params = {
            "objective": "regression",
            "metric": ["rmse"],
            "num_leaves": 15,
            "learning_rate": 0.1,
            "verbose": -1,
            "n_jobs": 1,
        }
        model = lgb.train(
            params,
            train_data,
            num_boost_round=50,
            valid_sets=[val_data],
            callbacks=[lgb.early_stopping(10, verbose=False)],
        )

        preds = model.predict(x_val)
        rmse = float(np.sqrt(mean_squared_error(y_val, preds)))
        r2 = float(r2_score(y_val, preds))

        assert rmse < 1.0, f"RMSE too high: {rmse}"
        assert r2 > 0.5, f"R2 too low: {r2}"

    def test_champion_selection(self):
        """champion model 선택 (RMSE 최소)"""
        from app.graph.subgraphs.modeling import select_champion

        models = [
            {"model_name": "subset_1", "cv_rmse": 0.45, "cv_mae": 0.35, "cv_r2": 0.85},
            {"model_name": "full_data", "cv_rmse": 0.50, "cv_mae": 0.40, "cv_r2": 0.82},
            {"model_name": "subset_2", "cv_rmse": 0.40, "cv_mae": 0.30, "cv_r2": 0.88},
        ]
        champion = select_champion(models)
        assert champion["model_name"] == "subset_2"
        assert champion["cv_rmse"] == 0.40


class TestSHAPSampling:
    """SHAP 샘플링 정책 테스트"""

    def test_shap_sampling_trigger(self):
        """5000행 초과 시 샘플링 적용"""
        from app.graph.subgraphs.shap_simplify import sample_for_shap

        max_shap_rows = 5000
        df = make_regression_df(n=6000)
        sampled, was_sampled = sample_for_shap(df, max_rows=max_shap_rows, seed=42)

        assert was_sampled is True
        assert len(sampled) == max_shap_rows

    def test_no_sampling_under_limit(self):
        """5000행 이하는 샘플링 없음"""
        from app.graph.subgraphs.shap_simplify import sample_for_shap

        df = make_regression_df(n=3000)
        sampled, was_sampled = sample_for_shap(df, max_rows=5000, seed=42)

        assert was_sampled is False
        assert len(sampled) == 3000

    def test_shap_values_computed(self):
        """SHAP 값 계산"""
        import lightgbm as lgb
        import shap
        from sklearn.model_selection import train_test_split

        df = make_regression_df(n=300)
        x = df[["feature_1", "feature_2"]].fillna(df[["feature_1", "feature_2"]].mean())
        y = df["target"]

        x_train, x_val, y_train, y_val = train_test_split(x, y, test_size=0.2, random_state=42)

        model = lgb.LGBMRegressor(n_estimators=30, verbose=-1)
        model.fit(x_train, y_train)

        explainer = shap.TreeExplainer(model)
        shap_values = explainer.shap_values(x_val)

        assert shap_values.shape == x_val.shape
