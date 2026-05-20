from __future__ import annotations

import logging
from pathlib import Path
import pandas as pd
import numpy as np
import joblib
from src.database import get_user_setting

logger = logging.getLogger(__name__)

class MLGuard:
    """Singleton for live inference using the trained XGBoost guard."""
    _instance = None
    DEFAULT_MODEL = "model_reg_20260517.joblib"

    def __init__(self):
        self.model = None
        self.feature_cols = []
        self.model_type = "classifier" # Default for backwards compatibility
        self._load_model()

    @classmethod
    def get_instance(cls) -> MLGuard:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def reload(self) -> None:
        """Force reload the model from disk using the latest database setting."""
        logger.info("Reloading ML Guard model...")
        self._load_model()

    def _load_model(self) -> None:
        model_filename = get_user_setting("ml_guard_model", self.DEFAULT_MODEL)
        model_path = Path(__file__).resolve().parent / "models" / model_filename
        
        if model_path.exists():
            try:
                model_data = joblib.load(model_path)
                self.model = model_data.get("model")
                
                # Force CPU inference for production environments. 
                # If the model was trained on GPU, it retains that state and will crash on CPU-only machines.
                if self.model is not None and hasattr(self.model, "set_params"):
                    self.model.set_params(device="cpu")

                self.feature_cols = model_data.get("feature_cols", [])
                self.model_type = model_data.get("type", "classifier")
                logger.info(f"Successfully loaded ML Guard (Type: {self.model_type}) from {model_path}")
            except Exception as e:
                logger.error(f"Failed to load ML Guard: {e}")
        else:
            logger.warning(f"ML Guard model not found at {model_path}")

    def score_setup(self, row: dict) -> float | None:
        """
        Returns the predicted score for the trade setup.
        For Classifiers: returns the probability (0.0 to 1.0) of a 'Good' trade.
        For Regressors: returns the predicted PnL percentage.
        Returns None if the model is not loaded.
        """
        if self.model is None or not self.feature_cols:
            return None

        # Construct the feature vector
        x_dict = {col: row.get(col, 0) for col in self.feature_cols}
        
        # XGBoost handles missing values natively, but for inference pandas DataFrame 
        # requires data to be present. Use NaN for missing instead of 0 to match training.
        x_df = pd.DataFrame([x_dict])
        x_df.replace({0: np.nan}, inplace=True) # Assuming missing features came in as 0

        try:
            if self.model_type == "regressor":
                # Regressor directly predicts continuous PnL percentage
                pred = self.model.predict(x_df)
                return float(pred[0])
            else:
                # predict_proba returns [prob_class_0, prob_class_1]
                proba = self.model.predict_proba(x_df)
                prob_good = proba[0][1]
                return float(prob_good)
        except Exception as e:
            logger.error(f"ML Guard inference error: {e}")
            return None
