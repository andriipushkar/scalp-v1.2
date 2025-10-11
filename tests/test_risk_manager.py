import pytest
from analysis.risk_manager import RiskManager

def test_calculate_position_size():
    """Tests the calculate_position_size function from RiskManager."""
    entry_price = 100.0
    take_profit_pct = 5.0
    expected_tp_price = 105.0

    tp_price = RiskManager.calculate_position_size(entry_price, take_profit_pct)

    assert tp_price == expected_tp_price, f"Expected {expected_tp_price}, but got {tp_price}"

def test_calculate_position_size_zero_pct():
    """Tests with zero percentage."""
    entry_price = 120.0
    take_profit_pct = 0.0
    expected_tp_price = 120.0

    tp_price = RiskManager.calculate_position_size(entry_price, take_profit_pct)

    assert tp_price == expected_tp_price, f"Expected {expected_tp_price}, but got {tp_price}"
