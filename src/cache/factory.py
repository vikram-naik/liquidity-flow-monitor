import os
from .interface import CacheInterface
from .redis_provider import RedisCache

_cache_instance = None

def get_cache() -> CacheInterface:
    """
    Singleton factory to get the configured cache provider.
    """
    global _cache_instance
    if _cache_instance is None:
        # Default to Redis for now, can be extended for other providers
        # if provider == 'redis':
        _cache_instance = RedisCache()
        
    return _cache_instance
