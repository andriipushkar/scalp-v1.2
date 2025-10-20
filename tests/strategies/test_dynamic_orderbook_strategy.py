import pytest
import pandas as pd
from unittest.mock import MagicMock

from strategies.dynamic_orderbook_strategy import DynamicOrderbookStrategy
from binance.enums import SIDE_BUY, SIDE_SELL

# --- Фікстури ---

@pytest.fixture
def dynamic_strategy_params():
    """Надає базовий набір параметрів для DynamicOrderbookStrategy."""
    return {
        "entry_order_type": "MARKET",
        "stop_loss_percent": 1.0,
        "initial_tp_search_percent": 2.0,
        "trailing_sl_distance_percent": 0.5,
        "pre_emptive_close_threshold_mult": 2.0,
        "wall_volume_multiplier": 10,
        "activation_distance_ticks": 15,
        "max_spread_bps": 5, # New parameter
        "min_wall_volume": 100 # New parameter
    }

@pytest.fixture
def mock_order_book_manager():
    """Створює мок (заглушку) для OrderBookManager."""
    mock = MagicMock()
    # За замовчуванням повертаємо порожні DataFrame, щоб уникнути помилок індексації
    mock.get_bids.return_value = pd.DataFrame(columns=['quantity'], index=pd.Index([], name='price'))
    mock.get_asks.return_value = pd.DataFrame(columns=['quantity'], index=pd.Index([], name='price'))
    mock.get_best_bid.return_value = None
    mock.get_best_ask.return_value = None
    return mock

# --- Тести для calculate_sl_tp ---

def test_calculate_sl_tp_long_with_liquidity(dynamic_strategy_params, mock_order_book_manager):
    """ТЕСТ: Розрахунок SL/TP для LONG позиції з ліквідністю в стакані."""
    strategy = DynamicOrderbookStrategy("test_dynamic_long", "BTCUSDT", dynamic_strategy_params)
    entry_price = 100.0
    
    # Імітуємо аски для TP
    asks_data = pd.DataFrame({'quantity': [10, 50, 20]}, index=pd.Index([101.0, 102.0, 103.0], name='price'))
    mock_order_book_manager.get_asks.return_value = asks_data

    sl_tp = strategy.calculate_sl_tp(entry_price, 'Long', mock_order_book_manager)

    expected_sl = entry_price * (1 - dynamic_strategy_params['stop_loss_percent'] / 100)
    # Найбільший об'єм знаходиться на 102.0, що в межах 2% (100 * 1.02 = 102)
    expected_tp = 102.0 

    assert sl_tp is not None
    assert pytest.approx(sl_tp['stop_loss']) == expected_sl
    assert pytest.approx(sl_tp['take_profit']) == expected_tp

def test_calculate_sl_tp_long_no_liquidity_within_range(dynamic_strategy_params, mock_order_book_manager):
    """ТЕСТ: Розрахунок SL/TP для LONG позиції без ліквідності в діапазоні TP."""
    strategy = DynamicOrderbookStrategy("test_dynamic_long_no_liq", "BTCUSDT", dynamic_strategy_params)
    entry_price = 100.0
    
    # Імітуємо аски, але за межами діапазону 2% (102.0)
    asks_data = pd.DataFrame({'quantity': [10, 20]}, index=pd.Index([102.5, 103.0], name='price'))
    mock_order_book_manager.get_asks.return_value = asks_data

    sl_tp = strategy.calculate_sl_tp(entry_price, 'Long', mock_order_book_manager)

    expected_sl = entry_price * (1 - dynamic_strategy_params['stop_loss_percent'] / 100)
    # TP має бути на максимальній межі 2% (100 * 1.02 = 102)
    expected_tp = entry_price * (1 + dynamic_strategy_params['initial_tp_search_percent'] / 100)

    assert sl_tp is not None
    assert pytest.approx(sl_tp['stop_loss']) == expected_sl
    assert pytest.approx(sl_tp['take_profit']) == expected_tp

def test_calculate_sl_tp_short_with_liquidity(dynamic_strategy_params, mock_order_book_manager):
    """ТЕСТ: Розрахунок SL/TP для SHORT позиції з ліквідністю в стакані."""
    strategy = DynamicOrderbookStrategy("test_dynamic_short", "BTCUSDT", dynamic_strategy_params)
    entry_price = 100.0
    
    # Імітуємо біди для TP
    bids_data = pd.DataFrame({'quantity': [20, 60, 15]}, index=pd.Index([99.0, 98.0, 97.0], name='price'))
    mock_order_book_manager.get_bids.return_value = bids_data

    sl_tp = strategy.calculate_sl_tp(entry_price, 'Short', mock_order_book_manager)

    expected_sl = entry_price * (1 + dynamic_strategy_params['stop_loss_percent'] / 100)
    # Найбільший об'єм знаходиться на 98.0, що в межах 2% (100 * 0.98 = 98)
    expected_tp = 98.0

    assert sl_tp is not None
    assert pytest.approx(sl_tp['stop_loss']) == expected_sl
    assert pytest.approx(sl_tp['take_profit']) == expected_tp

def test_calculate_sl_tp_short_no_liquidity_within_range(dynamic_strategy_params, mock_order_book_manager):
    """ТЕСТ: Розрахунок SL/TP для SHORT позиції без ліквідності в діапазоні TP."""
    strategy = DynamicOrderbookStrategy("test_dynamic_short_no_liq", "BTCUSDT", dynamic_strategy_params)
    entry_price = 100.0
    
    # Імітуємо біди, але за межами діапазону 2% (98.0)
    bids_data = pd.DataFrame({'quantity': [10, 20]}, index=pd.Index([97.5, 97.0], name='price'))
    mock_order_book_manager.get_bids.return_value = bids_data

    sl_tp = strategy.calculate_sl_tp(entry_price, 'Short', mock_order_book_manager)

    expected_sl = entry_price * (1 + dynamic_strategy_params['stop_loss_percent'] / 100)
    # TP має бути на мінімальній межі 2% (100 * 0.98 = 98)
    expected_tp = entry_price * (1 - dynamic_strategy_params['initial_tp_search_percent'] / 100)

    assert sl_tp is not None
    assert pytest.approx(sl_tp['stop_loss']) == expected_sl
    assert pytest.approx(sl_tp['take_profit']) == expected_tp

# --- Тести для analyze_and_adjust ---

def test_analyze_and_adjust_long_trailing_sl(dynamic_strategy_params, mock_order_book_manager):
    """ТЕСТ: Trailing SL для LONG позиції, коли ціна зростає."""
    strategy = DynamicOrderbookStrategy("test_dynamic_long_trail", "BTCUSDT", dynamic_strategy_params)
    position = {
        "side": "Long",
        "entry_price": 100.0,
        "stop_loss": 99.0, # Поточний SL
        "take_profit": 102.0,
        "quantity": 1.0
    }
    
    # Імітуємо зростання ціни
    mock_order_book_manager.get_best_bid.return_value = 100.8 # Ціна зросла

    adjustment = strategy.analyze_and_adjust(position, mock_order_book_manager)

    assert adjustment is not None
    assert adjustment['command'] == 'ADJUST_TP_SL'
    # Новий SL має бути 100.8 * (1 - 0.005) = 100.296, що більше за 99.0
    expected_new_sl = 100.8 * (1 - dynamic_strategy_params['trailing_sl_distance_percent'] / 100)
    assert pytest.approx(adjustment['stop_loss']) == expected_new_sl
    assert pytest.approx(adjustment['take_profit']) == 102.816 # Очікуємо, що TP також буде скориговано
def test_analyze_and_adjust_short_trailing_sl(dynamic_strategy_params, mock_order_book_manager):
    """ТЕСТ: Trailing SL для SHORT позиції, коли ціна падає."""
    strategy = DynamicOrderbookStrategy("test_dynamic_short_trail", "BTCUSDT", dynamic_strategy_params)
    position = {
        "side": "Short",
        "entry_price": 100.0,
        "stop_loss": 101.0, # Поточний SL
        "take_profit": 98.0,
        "quantity": 1.0
    }
    
    # Імітуємо падіння ціни
    mock_order_book_manager.get_best_ask.return_value = 99.2 # Ціна впала

    adjustment = strategy.analyze_and_adjust(position, mock_order_book_manager)

    assert adjustment is not None
    assert adjustment['command'] == 'ADJUST_TP_SL'
    # Новий SL має бути 99.2 * (1 + 0.005) = 99.696, що менше за 101.0
    expected_new_sl = 99.2 * (1 + dynamic_strategy_params['trailing_sl_distance_percent'] / 100)
    assert pytest.approx(adjustment['stop_loss']) == expected_new_sl
    assert pytest.approx(adjustment['take_profit']) == 97.216 # Очікуємо, що TP також буде скориговано
def test_analyze_and_adjust_long_pre_emptive_close(dynamic_strategy_params, mock_order_book_manager):
    """ТЕСТ: Випереджувальне закриття LONG позиції при тиску на продаж."""
    strategy = DynamicOrderbookStrategy("test_dynamic_long_pre_close", "BTCUSDT", dynamic_strategy_params)
    position = {
        "side": "Long",
        "entry_price": 100.0,
        "stop_loss": 99.0,
        "take_profit": 102.0,
        "quantity": 1.0
    }
    
    mock_order_book_manager.get_best_bid.return_value = 100.0
    # Імітуємо сильний тиск на продаж (asks >> bids)
    mock_order_book_manager.get_asks.return_value = pd.DataFrame({'quantity': [200, 10]}, index=pd.Index([100.05, 100.1], name='price'))
    mock_order_book_manager.get_bids.return_value = pd.DataFrame({'quantity': [50, 10]}, index=pd.Index([99.95, 99.9], name='price'))

    adjustment = strategy.analyze_and_adjust(position, mock_order_book_manager)

    assert adjustment is not None
    assert adjustment['command'] == 'CLOSE_POSITION'

def test_analyze_and_adjust_short_pre_emptive_close(dynamic_strategy_params, mock_order_book_manager):
    """ТЕСТ: Випереджувальне закриття SHORT позиції при тиску на купівлю."""
    strategy = DynamicOrderbookStrategy("test_dynamic_short_pre_close", "BTCUSDT", dynamic_strategy_params)
    position = {
        "side": "Short",
        "entry_price": 100.0,
        "stop_loss": 101.0,
        "take_profit": 98.0,
        "quantity": 1.0
    }
    
    mock_order_book_manager.get_best_ask.return_value = 100.0
    # Імітуємо сильний тиск на купівлю (bids >> asks)
    mock_order_book_manager.get_bids.return_value = pd.DataFrame({'quantity': [200, 10]}, index=pd.Index([99.95, 99.9], name='price'))
    mock_order_book_manager.get_asks.return_value = pd.DataFrame({'quantity': [50, 10]}, index=pd.Index([100.05, 100.1], name='price'))

    adjustment = strategy.analyze_and_adjust(position, mock_order_book_manager)

    assert adjustment is not None
    assert adjustment['command'] == 'CLOSE_POSITION'

def test_analyze_and_adjust_no_action_needed(dynamic_strategy_params, mock_order_book_manager):
    """ТЕСТ: Жодних дій не потрібно, якщо умови для коригування не виконані."""
    strategy = DynamicOrderbookStrategy("test_dynamic_no_action", "BTCUSDT", dynamic_strategy_params)
    position = {
        "side": "Long",
        "entry_price": 100.0,
        "stop_loss": 99.0,
        "take_profit": 102.0,
        "quantity": 1.0
    }
    
    mock_order_book_manager.get_best_bid.return_value = 100.0 # Ціна не змінилася або змінилася недостатньо
    # Збалансований стакан
    mock_order_book_manager.get_asks.return_value = pd.DataFrame({'quantity': [100]}, index=pd.Index([100.05], name='price'))
    mock_order_book_manager.get_bids.return_value = pd.DataFrame({'quantity': [100]}, index=pd.Index([99.95], name='price'))

    adjustment = strategy.analyze_and_adjust(position, mock_order_book_manager)

    assert adjustment is None

# --- Тести для check_signal ---

def test_check_signal_long_on_bid_wall(dynamic_strategy_params, mock_order_book_manager):
    """ТЕСТ: Генерується LONG сигнал, коли є стіна на купівлю."""
    # Готуємо дані: 19 звичайних ордерів і одна велика стіна
    normal_bids = [[4099.0 - i*0.5, 10] for i in range(19)]
    wall_bid = [4100.0, 200] # Стіна (об'єм 200) > сума інших (19*10=190)
    bids_list = [wall_bid] + normal_bids
    
    bids_data = {'price': [row[0] for row in bids_list], 'quantity': [row[1] for row in bids_list]}
    asks_data = {'price': [4100.1, 4100.2], 'quantity': [10, 12]} # Best ask close to wall
    mock_order_book_manager.get_bids.return_value = pd.DataFrame(bids_data).set_index('price')
    mock_order_book_manager.get_asks.return_value = pd.DataFrame(asks_data).set_index('price')

    strategy = DynamicOrderbookStrategy("test_dynamic_signal_long", "BTCUSDT", dynamic_strategy_params)
    # Mock _get_tick_size as it's an internal helper
    strategy._get_tick_size = MagicMock(return_value=0.1)

    signal = strategy.check_signal(mock_order_book_manager)

    assert signal is not None
    assert signal['signal_type'] == 'Long'
    assert signal['wall_price'] == 4100.0

def test_check_signal_short_on_ask_wall(dynamic_strategy_params, mock_order_book_manager):
    """ТЕСТ: Генерується SHORT сигнал, коли є стіна на продаж."""
    # Робимо ціну купівлі ближчою до стіни, щоб пройти перевірку відстані
    bids_data = {'price': [4109.9, 4109.8], 'quantity': [10, 8]} # Best bid close to wall
    # Готуємо дані: 19 звичайних ордерів і одна велика стіна
    normal_asks = [[4111.0 + i*0.5, 10] for i in range(19)]
    wall_ask = [4110.0, 250] # Стіна (250) > сума інших (190)
    asks_list = [wall_ask] + normal_asks

    asks_data = {'price': [row[0] for row in asks_list], 'quantity': [row[1] for row in asks_list]}
    mock_order_book_manager.get_bids.return_value = pd.DataFrame(bids_data).set_index('price')
    mock_order_book_manager.get_asks.return_value = pd.DataFrame(asks_data).set_index('price')

    strategy = DynamicOrderbookStrategy("test_dynamic_signal_short", "BTCUSDT", dynamic_strategy_params)
    strategy._get_tick_size = MagicMock(return_value=0.1)

    signal = strategy.check_signal(mock_order_book_manager)

    assert signal is not None
    assert signal['signal_type'] == 'Short'
    assert signal['wall_price'] == 4110.0

def test_check_signal_no_wall(dynamic_strategy_params, mock_order_book_manager):
    """ТЕСТ: Сигнал НЕ генерується, якщо немає явних стін."""
    # Об'єми приблизно однакові
    bids_data = {'price': [4100.0, 4099.0], 'quantity': [10, 8]}
    asks_data = {'price': [4100.1, 4100.2], 'quantity': [9, 12]}
    mock_order_book_manager.get_bids.return_value = pd.DataFrame(bids_data).set_index('price')
    mock_order_book_manager.get_asks.return_value = pd.DataFrame(asks_data).set_index('price')

    strategy = DynamicOrderbookStrategy("test_dynamic_no_wall", "BTCUSDT", dynamic_strategy_params)
    strategy._get_tick_size = MagicMock(return_value=0.1)

    signal = strategy.check_signal(mock_order_book_manager)

    assert signal is None

def test_check_signal_wall_too_far(dynamic_strategy_params, mock_order_book_manager):
    """ТЕСТ: Сигнал НЕ генерується, якщо стіна занадто далеко."""
    # Стіна є, але best_ask_price занадто далеко від неї
    normal_bids = [[4000.0 - i*0.5, 10] for i in range(19)]
    wall_bid = [4000.0, 200]
    bids_list = [wall_bid] + normal_bids
    
    bids_data = {'price': [row[0] for row in bids_list], 'quantity': [row[1] for row in bids_list]}
    asks_data = {'price': [4005.0, 4005.1], 'quantity': [10, 12]} # Best ask far from wall
    mock_order_book_manager.get_bids.return_value = pd.DataFrame(bids_data).set_index('price')
    mock_order_book_manager.get_asks.return_value = pd.DataFrame(asks_data).set_index('price')

    strategy = DynamicOrderbookStrategy("test_dynamic_wall_too_far", "BTCUSDT", dynamic_strategy_params)
    strategy._get_tick_size = MagicMock(return_value=0.1)

    signal = strategy.check_signal(mock_order_book_manager)

    assert signal is None

def test_check_signal_wide_spread_no_signal(dynamic_strategy_params, mock_order_book_manager):
    """ТЕСТ: Сигнал НЕ генерується, якщо спред занадто широкий."""
    # Імітуємо широкий спред (наприклад, 10 bps, коли max_spread_bps = 5)
    bids_data = {'price': [4000.0, 3999.0], 'quantity': [200, 10]}
    asks_data = {'price': [4004.0, 4005.0], 'quantity': [10, 12]} # Спред 4004 - 4000 = 4.0. (4.0 / 4000.0) * 10000 = 10 bps
    mock_order_book_manager.get_bids.return_value = pd.DataFrame(bids_data).set_index('price')
    mock_order_book_manager.get_asks.return_value = pd.DataFrame(asks_data).set_index('price')

    strategy = DynamicOrderbookStrategy("test_dynamic_wide_spread", "BTCUSDT", dynamic_strategy_params)
    strategy._get_tick_size = MagicMock(return_value=0.1)

    signal = strategy.check_signal(mock_order_book_manager)

    assert signal is None

def test_check_signal_low_wall_volume_no_signal(dynamic_strategy_params, mock_order_book_manager):
    """ТЕСТ: Сигнал НЕ генерується, якщо об'єм стіни менший за мінімальний."""
    # Імітуємо стіну, але з об'ємом меншим за min_wall_volume (100)
    normal_bids = [[4099.0 - i*0.5, 10] for i in range(19)]
    wall_bid = [4100.0, 50] # Об'єм 50 < 100
    bids_list = [wall_bid] + normal_bids
    
    bids_data = {'price': [row[0] for row in bids_list], 'quantity': [row[1] for row in bids_list]}
    asks_data = {'price': [4100.1, 4100.2], 'quantity': [10, 12]} # Best ask close to wall
    mock_order_book_manager.get_bids.return_value = pd.DataFrame(bids_data).set_index('price')
    mock_order_book_manager.get_asks.return_value = pd.DataFrame(asks_data).set_index('price')

    strategy = DynamicOrderbookStrategy("test_dynamic_low_wall_vol", "BTCUSDT", dynamic_strategy_params)
    strategy._get_tick_size = MagicMock(return_value=0.1)

    signal = strategy.check_signal(mock_order_book_manager)

    assert signal is None
