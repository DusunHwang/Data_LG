"""최적화 라우팅 테스트"""

import numpy as np
import pandas as pd
import pytest


def make_simple_dataset(n=200, seed=42):
    """단순 회귀 데이터셋"""
    rng = np.random.default_rng(seed)
    X1 = rng.normal(0, 1, n)
    X2 = rng.normal(0, 1, n)
    y = 3 * X1 - 2 * X2 + rng.normal(0, 0.1, n)
    return pd.DataFrame({"f1": X1, "f2": X2, "target": y})


class TestOptimizerRouting:
    """최적화 전략 선택 테스트"""

    def test_grid_search_3_dims(self):
        """차원 <= 3: Grid Search 선택"""
        from app.graph.subgraphs.optimization import choose_optimizer

        search_space = {
            "num_leaves": [15, 31, 63],
            "learning_rate": [0.01, 0.05, 0.1],
            "feature_fraction": [0.8, 0.9],
        }
        # 3차원
        optimizer = choose_optimizer(search_space)
        assert optimizer == "grid_search"

    def test_optuna_4_dims(self):
        """차원 >= 4: Optuna 선택"""
        from app.graph.subgraphs.optimization import choose_optimizer

        search_space = {
            "num_leaves": [15, 31, 63],
            "learning_rate": [0.01, 0.05, 0.1],
            "feature_fraction": [0.8, 0.9],
            "bagging_fraction": [0.7, 0.8, 0.9],  # 4번째 차원
        }
        optimizer = choose_optimizer(search_space)
        assert optimizer == "optuna"

    def test_grid_search_exactly_3(self):
        """정확히 3차원: Grid Search"""
        from app.graph.subgraphs.optimization import choose_optimizer

        search_space = {
            "a": [1, 2],
            "b": [3, 4],
            "c": [5, 6],
        }
        assert choose_optimizer(search_space) == "grid_search"

    def test_optuna_exactly_4(self):
        """정확히 4차원: Optuna"""
        from app.graph.subgraphs.optimization import choose_optimizer

        search_space = {
            "a": [1, 2],
            "b": [3, 4],
            "c": [5, 6],
            "d": [7, 8],
        }
        assert choose_optimizer(search_space) == "optuna"


class TestGridSearch:
    """Grid Search 실행 테스트"""

    def test_grid_search_finds_best(self):
        """Grid Search가 최적 파라미터 반환"""
        import lightgbm as lgb
        from itertools import product
        from sklearn.model_selection import cross_val_score
        from sklearn.metrics import make_scorer, mean_squared_error
        import numpy as np

        df = make_simple_dataset(n=300)
        X = df[["f1", "f2"]]
        y = df["target"]

        search_space = {
            "num_leaves": [15, 31],
            "learning_rate": [0.05, 0.1],
        }

        keys = list(search_space.keys())
        values = list(search_space.values())
        best_rmse = float("inf")
        best_params = None

        for combo in product(*values):
            params = dict(zip(keys, combo))
            model = lgb.LGBMRegressor(
                verbose=-1,
                n_estimators=50,
                **params,
            )
            scores = cross_val_score(
                model, X, y, cv=3, scoring="neg_root_mean_squared_error"
            )
            rmse = float(-scores.mean())
            if rmse < best_rmse:
                best_rmse = rmse
                best_params = params

        assert best_params is not None
        assert best_rmse < 1.0
        assert set(best_params.keys()) == set(keys)


class TestOptunaSearch:
    """Optuna Search 테스트"""

    def test_optuna_minimizes_rmse(self):
        """Optuna 실행 후 RMSE 개선 확인"""
        import optuna
        import lightgbm as lgb
        from sklearn.model_selection import cross_val_score
        import numpy as np

        optuna.logging.set_verbosity(optuna.logging.WARNING)

        df = make_simple_dataset(n=300)
        X = df[["f1", "f2"]]
        y = df["target"]

        def objective(trial):
            params = {
                "num_leaves": trial.suggest_int("num_leaves", 10, 50),
                "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.2),
                "feature_fraction": trial.suggest_float("feature_fraction", 0.5, 1.0),
                "bagging_fraction": trial.suggest_float("bagging_fraction", 0.5, 1.0),
            }
            model = lgb.LGBMRegressor(verbose=-1, n_estimators=30, **params)
            scores = cross_val_score(model, X, y, cv=3, scoring="neg_root_mean_squared_error")
            return float(-scores.mean())

        study = optuna.create_study(direction="minimize")
        study.optimize(objective, n_trials=5)  # 빠른 테스트를 위해 5회만

        assert study.best_value < 1.0
        assert len(study.best_params) == 4


class TestSearchSpaceAnalysis:
    """Search Space 분석 테스트"""

    def test_count_dimensions(self):
        """차원 수 계산"""
        from app.graph.subgraphs.optimization import count_search_dimensions

        space = {"a": [1, 2, 3], "b": [4, 5], "c": [6, 7, 8, 9]}
        assert count_search_dimensions(space) == 3

    def test_empty_space(self):
        """빈 search space"""
        from app.graph.subgraphs.optimization import count_search_dimensions

        assert count_search_dimensions({}) == 0
