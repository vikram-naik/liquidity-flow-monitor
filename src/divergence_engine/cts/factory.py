from __future__ import annotations

from typing import Type
from .base import CTSStrategy
from .default_ema import DefaultEMAStrategy
from .dema import DEMAStrategy
from .kama import KAMAStrategy
from .savgol import SavgolStrategy


class CTSFactory:
    """Factory to provide the configured CTS calculation strategy."""

    _registry: dict[str, Type[CTSStrategy]] = {
        "default_ema": DefaultEMAStrategy,
        "dema": DEMAStrategy,
        "kama": KAMAStrategy,
        "savgol": SavgolStrategy,
    }

    @classmethod
    def get_strategy(cls, name: str = "default_ema", **kwargs) -> CTSStrategy:
        """
        Instantiate and return the requested strategy.
        
        Args:
            name: Name of the strategy to use (default_ema, dema, kama).
            **kwargs: Configuration flags passed to the strategy init.
            
        Returns:
            CTSStrategy instance.
        """
        strategy_cls = cls._registry.get(name.lower())
        if not strategy_cls:
            raise ValueError(f"Unknown CTS Strategy: '{name}'. Available strategies: {list(cls._registry.keys())}")
        
        return strategy_cls(**kwargs)
