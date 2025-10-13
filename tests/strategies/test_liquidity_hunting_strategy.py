import pytest
import pandas as pd
from unittest.mock import MagicMock

from strategies.liquidity_hunting_strategy import LiquidityHuntingStrategy

# --- Підготовчі дані та фікстури ---

@pytest.fixture
def strategy_params():
    """Надає базовий набір параметрів для стратегії."""
    return {
        'wall_volume_multiplier': 10,
        'activation_distance_ticks': 15,
        'stop_loss_pct': 0.005, # 0.5%
        'risk_reward_ratio': 1.5,
        'tp_offset_ticks': 10
    }

@pytest.fixture
def mock_order_book_manager():
    """Створює мок (заглушку) для OrderBookManager."""
    return MagicMock()

# --- Тести для check_signal --- 

def test_check_signal_long_on_bid_wall(strategy_params, mock_order_book_manager):
    """ТЕСТ: Генерується LONG сигнал, коли є стіна на купівлю."""
    # Готуємо дані: 19 звичайних ордерів і одна велика стіна
    normal_bids = [[4099.0 - i*0.5, 10] for i in range(19)]
    wall_bid = [4100.0, 200] # Стіна (об'єм 200) > сума інших (19*10=190)
    bids_list = [wall_bid] + normal_bids
    
    bids_data = {'price': [row[0] for row in bids_list], 'quantity': [row[1] for row in bids_list]}
    asks_data = {'price': [4101.0, 4102.0], 'quantity': [10, 12]}
    mock_order_book_manager.get_bids.return_value = pd.DataFrame(bids_data).set_index('price')
    mock_order_book_manager.get_asks.return_value = pd.DataFrame(asks_data).set_index('price')

    strategy = LiquidityHuntingStrategy("test_strat", "TESTUSDT", strategy_params)
    strategy._get_tick_size = MagicMock(return_value=0.1)

    signal = strategy.check_signal(mock_order_book_manager)

    assert signal is not None
    assert signal['signal_type'] == 'Long'
    assert signal['wall_price'] == 4100.0

    def test_check_signal_short_on_ask_wall(strategy_params, mock_order_book_manager):
        """ТЕСТ: Генерується SHORT сигнал, коли є стіна на продаж."""
        # Робимо ціну купівлі ближчою до стіни, щоб пройти перевірку відстані
        bids_data = {'price': [4109.0, 4108.0], 'quantity': [10, 8]}
        # Готуємо дані: 19 звичайних ордерів і одна велика стіна
        normal_asks = [[4111.0 + i*0.5, 10] for i in range(19)]
        wall_ask = [4110.0, 250] # Стіна (250) > сума інших (190)
        asks_list = [wall_ask] + normal_asks

        asks_data = {'price': [row[0] for row in asks_list], 'quantity': [row[1] for row in asks_list]}
        mock_order_book_manager.get_bids.return_value = pd.DataFrame(bids_data).set_index('price')
        mock_order_book_manager.get_asks.return_value = pd.DataFrame(asks_data).set_index('price')

        strategy = LiquidityHuntingStrategy("test_strat", "TESTUSDT", strategy_params)
        strategy._get_tick_size = MagicMock(return_value=0.1)

        signal = strategy.check_signal(mock_order_book_manager)

        assert signal is not None
        assert signal['signal_type'] == 'Short'
        assert signal['wall_price'] == 4110.0
def test_no_signal_if_no_wall(strategy_params, mock_order_book_manager):
    """ТЕСТ: Сигнал НЕ генерується, якщо немає явних стін."""
    # Об'єми приблизно однакові
    bids_data = {'price': [4100.0, 4099.0], 'quantity': [10, 8]}
    asks_data = {'price': [4101.0, 4102.0], 'quantity': [9, 12]}
    mock_order_book_manager.get_bids.return_value = pd.DataFrame(bids_data).set_index('price')
    mock_order_book_manager.get_asks.return_value = pd.DataFrame(asks_data).set_index('price')

    strategy = LiquidityHuntingStrategy("test_strat", "TESTUSDT", strategy_params)
    signal = strategy.check_signal(mock_order_book_manager)

    assert signal is None

# --- Тести для calculate_sl_tp ---

def test_calculate_sl_tp_long(strategy_params):
    """ТЕСТ: Розрахунок SL/TP для LONG позиції за новою логікою (%)."""
    strategy = LiquidityHuntingStrategy("test_strat", "TESTUSDT", strategy_params)
    entry_price = 4000.0
    
    sl_tp = strategy.calculate_sl_tp(entry_price, 'Long', order_book_manager=MagicMock(), tick_size=0.01)

    expected_sl = 4000.0 * (1 - 0.005) # 3980.0
    expected_risk = 4000.0 - expected_sl # 20.0
    expected_tp = 4000.0 + (expected_risk * 1.5) # 4030.0

    assert sl_tp is not None
    assert sl_tp['stop_loss'] == pytest.approx(expected_sl)
    assert sl_tp['take_profit'] == pytest.approx(expected_tp)

def test_calculate_sl_tp_short(strategy_params):
    """ТЕСТ: Розрахунок SL/TP для SHORT позиції за новою логікою (%)."""
    strategy = LiquidityHuntingStrategy("test_strat", "TESTUSDT", strategy_params)
    entry_price = 4000.0
    
    sl_tp = strategy.calculate_sl_tp(entry_price, 'Short', order_book_manager=MagicMock(), tick_size=0.01)

    expected_sl = 4000.0 * (1 + 0.005) # 4020.0
    expected_risk = expected_sl - 4000.0 # 20.0
    expected_tp = 4000.0 - (expected_risk * 1.5) # 3970.0

    assert sl_tp is not None
    assert sl_tp['stop_loss'] == pytest.approx(expected_sl)
    assert sl_tp['take_profit'] == pytest.approx(expected_tp)

def test_calculate_sl_tp_fallback_to_rr(strategy_params, mock_order_book_manager):
    """ТЕСТ: Розрахунок TP повертається до R/R, якщо протилежну стіну не знайдено."""
    # Імітуємо, що в стакані немає стін на продаж
    mock_order_book_manager.get_asks.return_value = pd.DataFrame({'price': [4101.0], 'quantity': [10]}).set_index('price')
    
    strategy = LiquidityHuntingStrategy("test_strat", "TESTUSDT", strategy_params)
    entry_price = 4000.0

    sl_tp = strategy.calculate_sl_tp(entry_price, 'Long', order_book_manager=mock_order_book_manager, tick_size=0.01)

    expected_sl = 4000.0 * (1 - 0.005)
    expected_risk = 4000.0 - expected_sl
    expected_tp_via_rr = 4000.0 + (expected_risk * 1.5)

    assert sl_tp is not None
    assert sl_tp['take_profit'] == pytest.approx(expected_tp_via_rr)
