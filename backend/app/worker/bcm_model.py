"""Bayesian Committee Machine (BCM) model.

Combines:
  - GaussianProcessRegressor with RBF kernel
  - GaussianProcessRegressor with DotProduct (linear) kernel
  - A pre-trained LGBMRegressor (champion model)

BCM formula merges the two GPR experts, then blends with LGBM 50/50.
"""

from __future__ import annotations

from typing import Callable, Optional

import numpy as np
import pandas as pd

MAX_GPR_ROWS = 300   # GPR is O(n^3) — keep small for tractability


class BCMModel:
    """Scikit-learn compatible wrapper for BCM (GPR×2 + LGBM)."""

    def __init__(self, lgbm_model, categorical_features: list | None = None):
        self.lgbm_model = lgbm_model
        self.categorical_features = categorical_features or []
        self.gpr_rbf = None
        self.gpr_linear = None
        self._prior_var: float = 1.0
        self._feature_names: list | None = None
        self._fitted = False

    # ------------------------------------------------------------------
    def fit(
        self,
        X: pd.DataFrame,
        y: "pd.Series | np.ndarray",
        progress_cb: Optional[Callable[[int, str], None]] = None,
    ) -> "BCMModel":
        from sklearn.gaussian_process import GaussianProcessRegressor
        from sklearn.gaussian_process.kernels import RBF, DotProduct, WhiteKernel
        from sklearn.preprocessing import StandardScaler

        def _cb(pct: int, msg: str):
            if progress_cb:
                progress_cb(pct, msg)

        _cb(0, "BCM 전처리 중...")

        y = np.asarray(y, dtype=float)
        self._prior_var = float(np.var(y)) + 1e-6
        self._feature_names = list(X.columns)

        # Drop categoricals — GPR requires numeric only
        num_cols = [c for c in X.columns if c not in self.categorical_features]
        X_num = X[num_cols].copy().select_dtypes(include="number").fillna(0)

        # Subsample for GPR tractability
        n = len(X_num)
        _cb(5, f"GPR 학습 데이터 준비 중... (전체 {n}행 → 최대 {MAX_GPR_ROWS}행 샘플링)")
        if n > MAX_GPR_ROWS:
            idx = np.random.choice(n, MAX_GPR_ROWS, replace=False)
            X_gpr = X_num.iloc[idx].values
            y_gpr = y[idx]
        else:
            X_gpr = X_num.values
            y_gpr = y

        # StandardScale
        self._scaler = StandardScaler().fit(X_gpr)
        Xs = self._scaler.transform(X_gpr)

        n_features = Xs.shape[1]
        length_scale = np.ones(n_features)

        # ── Expert 1: RBF kernel ───────────────────────────────────────
        _cb(15, f"GPR(RBF) 커널 학습 중... ({len(X_gpr)}행 × {n_features}피처)")
        kernel_rbf = (
            RBF(length_scale=length_scale, length_scale_bounds=(1e-2, 1e2))
            + WhiteKernel(noise_level=1e-2, noise_level_bounds=(1e-5, 1e1))
        )
        self.gpr_rbf = GaussianProcessRegressor(
            kernel=kernel_rbf,
            n_restarts_optimizer=0,   # 빠른 수렴 우선
            normalize_y=True,
            alpha=1e-6,
        ).fit(Xs, y_gpr)
        _cb(55, "GPR(RBF) 커널 학습 완료")

        # ── Expert 2: Linear (DotProduct) kernel ───────────────────────
        _cb(60, f"GPR(Linear) 커널 학습 중... ({len(X_gpr)}행 × {n_features}피처)")
        kernel_linear = (
            DotProduct(sigma_0=1.0, sigma_0_bounds=(1e-3, 1e3))
            + WhiteKernel(noise_level=1e-2, noise_level_bounds=(1e-5, 1e1))
        )
        self.gpr_linear = GaussianProcessRegressor(
            kernel=kernel_linear,
            n_restarts_optimizer=0,
            normalize_y=True,
            alpha=1e-6,
        ).fit(Xs, y_gpr)
        _cb(95, "GPR(Linear) 커널 학습 완료 — BCM 앙상블 준비")

        self._num_cols = num_cols
        self._fitted = True
        _cb(100, "BCM 학습 완료 (GPR×2 + LGBM 챔피언)")
        return self

    # ------------------------------------------------------------------
    def predict(self, X: "pd.DataFrame | pd.Series | np.ndarray") -> np.ndarray:
        if not self._fitted:
            raise RuntimeError("BCMModel.fit() must be called before predict()")

        if isinstance(X, np.ndarray):
            X = pd.DataFrame(X, columns=self._feature_names)

        # ── LGBM prediction ────────────────────────────────────────────
        try:
            mu_lgbm = np.asarray(self.lgbm_model.predict(X), dtype=float)
        except Exception:
            X2 = X.copy()
            for col in self.categorical_features:
                if col in X2.columns:
                    X2[col] = X2[col].astype("category")
            mu_lgbm = np.asarray(self.lgbm_model.predict(X2), dtype=float)

        # ── GPR prediction (numeric features only) ─────────────────────
        X_num = X[[c for c in self._num_cols if c in X.columns]].copy()
        for c in self._num_cols:
            if c not in X_num.columns:
                X_num[c] = 0.0
        X_num = X_num[self._num_cols].fillna(0)
        Xs = self._scaler.transform(X_num.values)

        mu1, std1 = self.gpr_rbf.predict(Xs, return_std=True)
        mu2, std2 = self.gpr_linear.predict(Xs, return_std=True)

        var1 = std1 ** 2 + 1e-10
        var2 = std2 ** 2 + 1e-10

        # BCM formula (M=2 experts)
        # σ²_bcm⁻¹ = σ₁⁻² + σ₂⁻² + (1-M)/σ²_prior
        M = 2
        inv_var_bcm = 1.0 / var1 + 1.0 / var2 + (1 - M) / self._prior_var
        inv_var_bcm = np.maximum(inv_var_bcm, 1e-10)
        var_bcm = 1.0 / inv_var_bcm
        mu_bcm = var_bcm * (mu1 / var1 + mu2 / var2)

        # 50 / 50 blend with LGBM champion
        return 0.5 * mu_bcm + 0.5 * mu_lgbm
