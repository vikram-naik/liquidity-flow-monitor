import sys
import os
import pandas as pd
import numpy as np
import unittest
from unittest.mock import MagicMock, patch

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../')))

from src.cache import get_cache
from src.cache.interface import CacheInterface
from src.cache.redis_provider import RedisCache

class TestCachePackage(unittest.TestCase):
    
    def test_factory_returns_interface(self):
        cache = get_cache()
        self.assertIsInstance(cache, CacheInterface)

    @patch('redis.Redis')
    def test_redis_provider(self, mock_redis):
        # Mock redis instance
        instance = mock_redis.return_value
        instance.ping.return_value = True
        
        cache = RedisCache()
        
        # Test Set
        df = pd.DataFrame({'a': [1, 2], 'b': [3, 4]})
        cache.set('test_key', df)
        self.assertTrue(instance.setex.called)
        
        # Test Get
        import pickle
        instance.get.return_value = pickle.dumps(df)
        result = cache.get('test_key')
        pd.testing.assert_frame_equal(df, result)
        
        # Test Delete
        cache.delete('test_key')
        self.assertTrue(instance.delete.called)

if __name__ == '__main__':
    unittest.main()
