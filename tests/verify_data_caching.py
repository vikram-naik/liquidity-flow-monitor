import sys
import os
import pandas as pd
import numpy as np
import unittest
from unittest.mock import MagicMock, patch

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../')))

from src.analysis.data import get_stock_data

class TestDataCaching(unittest.TestCase):
    
    @patch('src.analysis.data.cache')
    @patch('src.analysis.data.get_db_connection')
    @patch('src.analysis.data.get_or_create_anchor')
    @patch('src.analysis.data.calculate_volume_profile')
    def test_get_stock_data_cache_flow(self, mock_vp, mock_anchor, mock_db_conn, mock_cache):
        symbol = "TEST_SYM"
        df_mock = pd.DataFrame({
            'record_date': ['2023-01-01', '2023-01-02'],
            'price_close': [100.0, 105.0],
            'price_open': [99.0, 101.0],
            'price_high': [101.0, 106.0],
            'price_low': [98.0, 100.0],
            'volume_total': [1000, 1200],
            'delivery_qty': [500, 600]
        })
        
        # 1. Test Cache Miss
        mock_cache.get.return_value = None
        mock_conn = MagicMock()
        mock_db_conn.return_value = mock_conn
        
        with patch('pandas.read_sql', return_value=df_mock):
            mock_anchor.return_value = {'anchor_date': '2023-01-01'}
            mock_vp.return_value = []
            
            df, anchor, vp = get_stock_data(symbol)
            
            self.assertTrue(mock_cache.get.called)
            self.assertTrue(mock_db_conn.called)
            self.assertTrue(mock_cache.set.called)
            
        # 2. Test Cache Hit
        mock_cache.reset_mock()
        mock_db_conn.reset_mock()
        mock_cache.get.return_value = df_mock
        
        df, anchor, vp = get_stock_data(symbol)
        
        self.assertTrue(mock_cache.get.called)
        self.assertFalse(mock_db_conn.called)
        self.assertFalse(mock_cache.set.called)

if __name__ == '__main__':
    unittest.main()
