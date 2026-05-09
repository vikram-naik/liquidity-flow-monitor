from __future__ import annotations

import logging
from pathlib import Path
import pandas as pd
import joblib

logger = logging.getLogger(__name__)

class MLGuard:
    """Singleton for live inference using the trained XGBoost guard."""
    _instance = None
    ACTIVE_MODEL_VERSION = "20260509"

    def __init__(self):
        self.model = None
        self.feature_cols = []
        self._load_model()

    @classmethod
    def get_instance(cls) -> MLGuard:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _load_model(self) -> None:
        model_path = Path(__file__).resolve().parent / "models" / f"model_xgb_{self.ACTIVE_MODEL_VERSION}.joblib"
        if model_path.exists():
            try:
                model_data = joblib.load(model_path)
                self.model = model_data.get("model")
                self.feature_cols = model_data.get("feature_cols", [])
                logger.info(f"Successfully loaded ML Guard from {model_path}")
            except Exception as e:
                logger.error(f"Failed to load ML Guard: {e}")
        else:
            logger.warning(f"ML Guard model not found at {model_path}")

    def score_setup(self, row: dict) -> float | None:
        """
        Returns the probability (0.0 to 1.0) of a Good Setup.
        Returns None if the model is not loaded.
        """
        if self.model is None or not self.feature_cols:
            return None

        # Construct the feature vector exactly as the model expects
        x_dict = {col: row.get(col, 0) for col in self.feature_cols}
        x_df = pd.DataFrame([x_dict]).fillna(0)
        
        try:
            probs = self.model.predict_proba(x_df)
            # Class 1 is "Good Setup"
            prob_good = probs[0][1]
            return prob_good
        except Exception as e:
            logger.error(f"ML Guard inference error: {e}")
            return None
