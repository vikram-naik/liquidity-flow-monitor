"""
Marker Factory Pattern — Modular Signal Architecture.

Provides:
  - MarkerInterface (ABC): contract every marker must implement.
  - MarkerRegistry: auto-discovers and manages all registered markers.

Usage in data.py:
    from src.analysis.markers import MarkerRegistry
    registry = MarkerRegistry()
    for marker in registry.get_all():
        df = marker.evaluate(df)
"""

from abc import ABC, abstractmethod
from typing import Optional
import pandas as pd


class MarkerInterface(ABC):
    """
    Abstract base class for all signal markers.

    Lifecycle:
        1. ``evaluate(df)`` is called in the data pipeline to compute marker
           columns on the daily DataFrame.
        2. ``screen(df, latest, prev)`` is called by the screener to check
           whether the latest row qualifies.
        3. ``metadata()`` is consumed by the API to dynamically build legends,
           help modals, and chart rendering config.
    """

    # --- identity -----------------------------------------------------------

    @abstractmethod
    def name(self) -> str:
        """Unique machine-readable ID, e.g. ``'coil'``."""

    # --- computation --------------------------------------------------------

    @abstractmethod
    def evaluate(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Add marker columns to *df* and return it.

        The DataFrame arrives with shared prerequisite columns already
        computed (``atr_50``, ``dvl_slope_5``, ``deliv_sma_10``, …).
        """

    # --- screener -----------------------------------------------------------

    @abstractmethod
    def screen(self, df: pd.DataFrame, latest: pd.Series,
               prev: Optional[pd.Series]) -> bool:
        """
        Return ``True`` if the latest row qualifies for this marker's
        screener watchlist.

        Parameters
        ----------
        df   : Full recent DataFrame (at least 2 most recent rows).
        latest : ``df.iloc[-1]``.
        prev   : ``df.iloc[-2]`` or ``None`` if fewer than 2 rows.
        """

    # --- metadata -----------------------------------------------------------

    @abstractmethod
    def metadata(self) -> dict:
        """
        Return a dict describing this marker for the API / frontend.

        Required keys
        -------------
        id            : str   – same as ``name()``.
        label         : str   – human-readable name for the legend.
        is_chart_marker : bool – ``True`` if rendered on the candlestick chart.
        screener_name : str | None – watchlist name (e.g. ``'SCR: Ignition'``),
                        ``None`` if this marker has no screener.

        Optional keys (chart markers)
        -----------------------------
        color, shape, position, score_key, flag_key,
        help_title, help_html, legend_dot_style, text_format.
        """

    # --- debug / introspection ----------------------------------------------

    def debug_info(self, df: pd.DataFrame, row_idx: int) -> list[dict]:
        """
        Return a list of check dicts explaining why the marker was or was
        not triggered on the row at *row_idx*.

        Each dict should contain::

            {
                'label':     str,   # human-readable check name
                'value':     str,   # computed value (formatted)
                'threshold': str,   # threshold it's compared against
                'passed':    bool,  # did this check pass?
                'detail':    str,   # optional extra context
            }

        Default implementation returns an empty list.  Marker subclasses
        override this to expose their internal reasoning.
        """
        return []

    # --- aggregation --------------------------------------------------------

    def agg_rules(self) -> dict:
        """
        Return ``{column_name: agg_func}`` mappings used when resampling
        daily data to weekly / monthly.  Default: empty (no columns to
        aggregate).
        """
        return {}


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class MarkerRegistry:
    """
    Factory / registry that auto-discovers ``MarkerInterface`` subclasses.

    On first instantiation it imports every module in the ``markers`` package
    and collects concrete subclasses.  Results are cached at class level so
    the import scan happens only once.
    """

    _markers: list[MarkerInterface] | None = None

    def __init__(self):
        if MarkerRegistry._markers is None:
            MarkerRegistry._markers = self._discover()

    # -- public API ----------------------------------------------------------

    def get_all(self) -> list[MarkerInterface]:
        """Return all registered marker instances in evaluation order."""
        return list(MarkerRegistry._markers)

    def get_metadata(self) -> list[dict]:
        """Return metadata dicts for every registered marker."""
        return [m.metadata() for m in MarkerRegistry._markers]

    def get_screener_names(self) -> list[str]:
        """Return screener watchlist names for markers that have one."""
        names = []
        for m in MarkerRegistry._markers:
            sn = m.metadata().get('screener_name')
            if sn:
                names.append(sn)
        return names

    # -- discovery -----------------------------------------------------------

    @staticmethod
    def _discover() -> list[MarkerInterface]:
        """Import sibling modules and instantiate concrete subclasses."""
        import importlib
        import pkgutil
        import pathlib

        pkg_dir = pathlib.Path(__file__).parent
        instances: list[MarkerInterface] = []
        seen_names: set[str] = set()

        for finder, mod_name, _ in pkgutil.iter_modules([str(pkg_dir)]):
            if mod_name.startswith('_'):
                continue  # skip __init__, __pycache__, etc.
            importlib.import_module(f'{__package__}.{mod_name}')

        # Walk all concrete subclasses (including transitive)
        def _collect(cls):
            for sub in cls.__subclasses__():
                if not getattr(sub, '__abstractmethods__', None):
                    inst = sub()
                    if inst.name() not in seen_names:
                        instances.append(inst)
                        seen_names.add(inst.name())
                _collect(sub)

        _collect(MarkerInterface)

        # Sort by a declared ``_order`` attribute; fall back to 50.
        instances.sort(key=lambda m: getattr(m, '_order', 50))
        return instances
