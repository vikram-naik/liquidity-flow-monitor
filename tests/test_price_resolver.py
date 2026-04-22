
import unittest
from src.trading.price_resolver.historical import HistoricalResolver
from src.trading.price_resolver.base import PriceContext

class TestHistoricalResolver(unittest.TestCase):
    def setUp(self):
        self.resolver = HistoricalResolver()

    def test_buy_resolution(self):
        ctx = PriceContext(side="BUY", symbol="TEST", close=100.0, open=90.0)
        res = self.resolver.resolve(ctx)
        self.assertEqual(res.limit_price, 100.0)
        self.assertIn("execution-day close", res.rationale)

    def test_sell_regular_resolution(self):
        ctx = PriceContext(side="SELL", symbol="TEST", close=100.0, open=95.0)
        res = self.resolver.resolve(ctx)
        self.assertEqual(res.limit_price, 95.0)
        self.assertIn("execution-day open", res.rationale)

    def test_sell_manual_exit_resolution(self):
        # User requested manual exit to use close price
        ctx = PriceContext(side="SELL", symbol="TEST", exit_reason="manual_exit", close=102.5, open=100.0)
        res = self.resolver.resolve(ctx)
        self.assertEqual(res.limit_price, 102.5)
        self.assertIn("manual exit at close", res.rationale)

    def test_sell_fallback_resolution(self):
        ctx = PriceContext(side="SELL", symbol="TEST", close=105.0, open=0.0)
        res = self.resolver.resolve(ctx)
        self.assertEqual(res.limit_price, 105.0)
        self.assertIn("fallback to close", res.rationale)

if __name__ == "__main__":
    unittest.main()
