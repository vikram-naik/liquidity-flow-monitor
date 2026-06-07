import unittest
import os
import tempfile
import json
import shutil
import math
from unittest.mock import patch, MagicMock

import src.database
import src.trading.signals.savgol_cts.symbol_configs as sc
from src.trading.signals.savgol_cts.config import SavgolCTSEntryConfig
from scripts.weekly_bwo_sync import calculate_bwo_score, serialize_and_save_config

class TestWeeklyBwoSync(unittest.TestCase):
    def setUp(self):
        # Create temp directory for testing configs and exclusion list
        self.test_dir = tempfile.mkdtemp()
        self.orig_bw_configs_dir = src.database.BW_CONFIGS_DIR
        self.orig_excluded_symbols_path = src.database.EXCLUDED_SYMBOLS_PATH
        
        # Patch the configuration variables in both modules
        src.database.BW_CONFIGS_DIR = self.test_dir
        src.database.EXCLUDED_SYMBOLS_PATH = os.path.join(self.test_dir, "excluded_symbols.json")
        sc.BW_CONFIGS_DIR = self.test_dir
        sc.EXCLUDED_SYMBOLS_PATH = os.path.join(self.test_dir, "excluded_symbols.json")

    def tearDown(self):
        # Restore variables
        src.database.BW_CONFIGS_DIR = self.orig_bw_configs_dir
        src.database.EXCLUDED_SYMBOLS_PATH = self.orig_excluded_symbols_path
        sc.BW_CONFIGS_DIR = self.orig_bw_configs_dir
        sc.EXCLUDED_SYMBOLS_PATH = self.orig_excluded_symbols_path
        
        # Clean up temp directory
        shutil.rmtree(self.test_dir)

    def test_exclusion_list_management(self):
        sym = "TESTSYM"
        
        # Initially, the symbol should not be excluded
        self.assertFalse(sc.is_symbol_excluded(sym))
        
        # Exclude the symbol
        sc.add_to_exclusion_list(sym)
        self.assertTrue(sc.is_symbol_excluded(sym))
        
        # Exclude again to verify idempotency
        sc.add_to_exclusion_list(sym)
        self.assertTrue(sc.is_symbol_excluded(sym))
        
        # Remove exclusion
        sc.remove_from_exclusion_list(sym)
        self.assertFalse(sc.is_symbol_excluded(sym))

    def test_get_symbol_entry_config_when_excluded(self):
        sym = "TESTSYM"
        sc.add_to_exclusion_list(sym)
        
        cfg = sc.get_symbol_entry_config(sym)
        self.assertIsInstance(cfg, SavgolCTSEntryConfig)
        
        # Verify all entry paths are completely disabled
        self.assertFalse(cfg.trend_pullback_enabled)
        self.assertFalse(cfg.cdvl_cts.enabled)
        self.assertFalse(cfg.universal_cross.enabled)
        self.assertFalse(cfg.flow_momentum.enabled)
        self.assertFalse(cfg.coherent_pullback.enabled)
        self.assertFalse(cfg.anchor_shock_pullback.enabled)
        self.assertFalse(cfg.springboard.enabled)
        self.assertFalse(cfg.oversold_decel.enabled)
        self.assertFalse(cfg.custom_bayesian.enabled)

    def test_bwo_score_calculation(self):
        # Test basic score computation:
        # Score = -250.0 * sl_ratio + 10 * min(trades, 15) + avg_pnl
        # trades = 10, sl_hits = 1 (sl_ratio = 0.1), avg_pnl = 5.0
        # Expected: -250 * 0.1 + 10 * 10 + 5.0 = -25 + 100 + 5.0 = 80.0
        score = calculate_bwo_score(10, 5.0, 1)
        self.assertAlmostEqual(score, 80.0)

        # trades = 20 (capped at 15 for bonus), sl_hits = 0 (sl_ratio = 0.0), avg_pnl = 12.5
        # Expected: 0 + 10 * 15 + 12.5 = 162.5
        score = calculate_bwo_score(20, 12.5, 0)
        self.assertAlmostEqual(score, 162.5)

    def test_serialize_and_save_config(self):
        sym = "MOCKSYM"
        details = {
            "score_threshold": 2.5,
            "feature_bins": {
                "cwc": [-float('inf'), 0.5, float('inf')],
                "cts": [-float('inf'), -0.2, 0.3, float('inf')]
            },
            "feature_weights": {
                "cwc": [
                    [-float('inf'), 0.5, 0.12],
                    [0.5, float('inf'), -0.34]
                ],
                "cts": [
                    [-float('inf'), -0.2, 0.45],
                    [-0.2, 0.3, -0.1],
                    [0.3, float('inf'), 0.67]
                ]
            },
            "accumulation": {
                "score_threshold": 1.5,
                "feature_bins": {
                    "cwc": [-float('inf'), 0.4, float('inf')]
                },
                "feature_weights": {
                    "cwc": [
                        [-float('inf'), 0.4, 0.15],
                        [0.4, float('inf'), -0.25]
                    ]
                }
            },
            "momentum": {
                "score_threshold": 3.0,
                "feature_bins": {
                    "cwc": [-float('inf'), 0.6, float('inf')]
                },
                "feature_weights": {
                    "cwc": [
                        [-float('inf'), 0.6, 0.22],
                        [0.6, float('inf'), -0.42]
                    ]
                }
            }
        }
        
        # Serialize and save configuration
        out_path = serialize_and_save_config(sym, details, self.test_dir)
        self.assertTrue(os.path.exists(out_path))
        
        # Check generated file contents
        with open(out_path, "r") as f:
            saved_data = json.load(f)
            
        cb = saved_data["custom_bayesian"]
        self.assertTrue(cb["enabled"])
        self.assertEqual(cb["score_threshold"], 2.5)
        
        # Verify bins have infinity strings
        self.assertEqual(cb["feature_bins"]["cwc"], ["-inf", 0.5, "inf"])
        self.assertEqual(cb["feature_bins"]["cts"], ["-inf", -0.2, 0.3, "inf"])
        
        # Verify sub-models
        self.assertEqual(cb["accumulation"]["score_threshold"], 1.5)
        self.assertEqual(cb["accumulation"]["feature_bins"]["cwc"], ["-inf", 0.4, "inf"])
        
        self.assertEqual(cb["momentum"]["score_threshold"], 3.0)
        self.assertEqual(cb["momentum"]["feature_bins"]["cwc"], ["-inf", 0.6, "inf"])
        
        # Test loading back via load_bw_override
        loaded_override = sc.load_bw_override(sym)
        self.assertIsNotNone(loaded_override)
        
        # Check parsed infinities in get_symbol_entry_config
        loaded_cfg = sc.get_symbol_entry_config(sym)
        cb_cfg = loaded_cfg.custom_bayesian
        self.assertTrue(cb_cfg.enabled)
        self.assertEqual(cb_cfg.score_threshold, 2.5)
        self.assertEqual(cb_cfg.feature_bins["cwc"], [float("-inf"), 0.5, float("inf")])
        self.assertEqual(cb_cfg.feature_weights["cwc"][0], (float("-inf"), 0.5, 0.12))
        
        # Verify parsed sub-models in get_symbol_entry_config
        self.assertEqual(cb_cfg.accumulation.score_threshold, 1.5)
        self.assertEqual(cb_cfg.accumulation.feature_bins["cwc"], [float("-inf"), 0.4, float("inf")])
        self.assertEqual(cb_cfg.accumulation.feature_weights["cwc"][0], (float("-inf"), 0.4, 0.15))
        
        self.assertEqual(cb_cfg.momentum.score_threshold, 3.0)
        self.assertEqual(cb_cfg.momentum.feature_bins["cwc"], [float("-inf"), 0.6, float("inf")])
        self.assertEqual(cb_cfg.momentum.feature_weights["cwc"][0], (float("-inf"), 0.6, 0.22))

if __name__ == "__main__":
    unittest.main()
