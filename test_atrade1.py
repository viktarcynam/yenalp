import unittest
from atrade1 import create_occ_symbol

class TestCreateOccSymbol(unittest.TestCase):

    def test_valid_call(self):
        """Test a standard valid call option."""
        result = create_occ_symbol("AAPL", "2024-01-19", "C", "190")
        self.assertEqual(result, "AAPL240119C00190000")

    def test_valid_put_with_float_strike(self):
        """Test a valid put option with a floating point strike."""
        result = create_occ_symbol("SPY", "2025-03-21", "P", "455.5")
        self.assertEqual(result, "SPY250321P00455500")

    def test_lowercase_inputs(self):
        """Test that lowercase inputs are correctly formatted."""
        result = create_occ_symbol("tsla", "2024-02-16", "p", "200")
        self.assertEqual(result, "TSLA240216P00200000")

    def test_invalid_date_format(self):
        """Test an incorrect date format."""
        result = create_occ_symbol("AMD", "2024/01/20", "C", "150")
        self.assertIsNone(result)

    def test_invalid_option_type(self):
        """Test an invalid option type."""
        result = create_occ_symbol("NVDA", "2024-06-21", "X", "500")
        self.assertIsNone(result)

    def test_invalid_strike_price(self):
        """Test a non-numeric strike price."""
        result = create_occ_symbol("MSFT", "2024-04-19", "C", "four-hundred")
        self.assertIsNone(result)

    def test_zero_strike_price(self):
        """Test a zero strike price."""
        result = create_occ_symbol("GOOG", "2024-12-20", "C", "0")
        self.assertEqual(result, "GOOG241220C00000000")

if __name__ == '__main__':
    unittest.main()
