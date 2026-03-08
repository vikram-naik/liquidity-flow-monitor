from abc import ABC, abstractmethod
from typing import Any, Optional

class CacheInterface(ABC):
    """
    Abstract interface for cache providers.
    """
    
    @abstractmethod
    def get(self, key: str) -> Optional[Any]:
        """Retrieve an item from the cache."""
        pass
    
    @abstractmethod
    def set(self, key: str, value: Any, ttl: int = 3600) -> bool:
        """Store an item in the cache with a time-to-live in seconds."""
        pass
    
    @abstractmethod
    def delete(self, key: str) -> bool:
        """Remove an item from the cache."""
        pass
    
    @abstractmethod
    def clear(self) -> bool:
        """Clear all entries from the cache."""
        pass

    @abstractmethod
    def delete_pattern(self, pattern: str) -> int:
        """Remove all keys matching a glob pattern. Returns count deleted."""
        pass
