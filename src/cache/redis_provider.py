import redis
import pickle
import os
import logging
from typing import Any, Optional
from .interface import CacheInterface

logger = logging.getLogger(__name__)

class RedisCache(CacheInterface):
    """
    Redis implementation of the CacheInterface.
    """
    
    def __init__(self, host: str = 'localhost', port: int = 6379, db: int = 0, password: Optional[str] = None):
        redis_url = os.getenv("REDIS_URL")
        if redis_url:
            self.client = redis.from_url(redis_url)
        else:
            host = os.getenv("REDIS_HOST", host)
            port = int(os.getenv("REDIS_PORT", port))
            db = int(os.getenv("REDIS_DB", db))
            password = os.getenv("REDIS_PASSWORD", password)
            self.client = redis.Redis(host=host, port=port, db=db, password=password)
        
        try:
            self.client.ping()
            logger.info("Connected to Redis successfully.")
        except redis.ConnectionError as e:
            logger.error(f"Could not connect to Redis: {e}")
            self.client = None

    def get(self, key: str) -> Optional[Any]:
        if not self.client:
            return None
        
        try:
            data = self.client.get(key)
            if data:
                return pickle.loads(data)
        except Exception as e:
            logger.error(f"Error getting key {key} from Redis: {e}")
        return None

    def set(self, key: str, value: Any, ttl: int = 3600) -> bool:
        if not self.client:
            return False
        
        try:
            data = pickle.dumps(value)
            return self.client.setex(key, ttl, data)
        except Exception as e:
            logger.error(f"Error setting key {key} in Redis: {e}")
            return False

    def delete(self, key: str) -> bool:
        if not self.client:
            return False
        
        try:
            return bool(self.client.delete(key))
        except Exception as e:
            logger.error(f"Error deleting key {key} from Redis: {e}")
            return False

    def clear(self) -> bool:
        if not self.client:
            return False
        
        try:
            return self.client.flushdb()
        except Exception as e:
            logger.error(f"Error clearing Redis DB: {e}")
            return False
