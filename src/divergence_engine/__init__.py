"""
Multi-Anchor Divergence Engine — Stock Trend Analysis System.

Analyses NSE EOD OHLC + Volume + Delivery data through rolling anchor
windows [10, 30, 60, 120] to classify trend state, accumulation,
distribution, and divergence with probability/confidence scoring.

Usage::

    from src.divergence_engine.engine import DivergenceEngine

    engine = DivergenceEngine(ticker='RELIANCE')
    results = engine.run()

    results.ledger    # full DVL ledger DataFrame
    results.states    # state + probability per bar
    results.export()  # saves ledger CSV
"""

__version__ = "1.0.0"
