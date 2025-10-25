
import pytest
import pandas as pd
from unittest.mock import MagicMock, AsyncMock

from strategies.ema_trend_following_strategy import EmaTrendFollowingStrategy

# --- Fixtures ---

@pytest.fixture
def strategy_params():
    """Provides a default set of parameters for the strategy."""
    return {
        'fast_ema_period': 5,
        'slow_ema_period': 10,
        'rsi_period': 14,
        'volume_ma_period': 20,
        'atr_period': 14,
        'sl_atr_multiplier': 1.5,
        'rr_ratio': 2.0,
        'kline_interval': '1m',
        'kline_limit': 15
    }

@pytest.fixture
def mock_binance_client():
    """Creates a mock for BinanceClient."""
    mock = MagicMock()
    mock.get_klines = AsyncMock()
    return mock

@pytest.fixture
def mock_order_book_manager():
    """Creates a mock for OrderBookManager."""
    return MagicMock()

# --- Helper function to create k-line data ---

def create_klines_data(base_price, count, trend='none'):
    """Generates sample k-line data for testing."""
    klines = []
    price = base_price
    for i in range(count):
        if trend == 'up':
            price += 0.1
        elif trend == 'down':
            price -= 0.1
        
        # Create some volume and other data
        open_p = price
        high_p = price + 0.05
        low_p = price - 0.05
        close_p = price
        volume = 100 + i * 5
        
        klines.append([0, open_p, high_p, low_p, close_p, volume, 0, 0, 0, 0, 0, 0])
    return klines

# --- Tests for calculate_sl_tp ---

def test_calculate_sl_tp_long(strategy_params, mock_order_book_manager):
    """Test SL/TP calculation for a Long signal."""
    strategy = EmaTrendFollowingStrategy("test_long_sl_tp", "BTCUSDT", strategy_params)
    entry_price = 100.0
    atr = 2.0
    tick_size = 0.01

    sl_tp = strategy.calculate_sl_tp(entry_price, 'Long', mock_order_book_manager, tick_size, atr=atr)

    expected_sl = entry_price - (strategy.sl_atr_multiplier * atr)
    expected_tp = entry_price + (strategy.rr_ratio * (entry_price - expected_sl))

    assert sl_tp is not None
    assert pytest.approx(sl_tp['stop_loss']) == round(expected_sl / tick_size) * tick_size
    assert pytest.approx(sl_tp['take_profit']) == round(expected_tp / tick_size) * tick_size

def test_calculate_sl_tp_short(strategy_params, mock_order_book_manager):
    """Test SL/TP calculation for a Short signal."""
    strategy = EmaTrendFollowingStrategy("test_short_sl_tp", "BTCUSDT", strategy_params)
    entry_price = 100.0
    atr = 2.0
    tick_size = 0.01

    sl_tp = strategy.calculate_sl_tp(entry_price, 'Short', mock_order_book_manager, tick_size, atr=atr)

    expected_sl = entry_price + (strategy.sl_atr_multiplier * atr)
    expected_tp = entry_price - (strategy.rr_ratio * (expected_sl - entry_price))

    assert sl_tp is not None
    assert pytest.approx(sl_tp['stop_loss']) == round(expected_sl / tick_size) * tick_size
    assert pytest.approx(sl_tp['take_profit']) == round(expected_tp / tick_size) * tick_size

def test_calculate_sl_tp_no_atr(strategy_params, mock_order_book_manager):
    """Test that SL/TP calculation fails without ATR."""
    strategy = EmaTrendFollowingStrategy("test_no_atr", "BTCUSDT", strategy_params)
    sl_tp = strategy.calculate_sl_tp(100.0, 'Long', mock_order_book_manager, 0.01, atr=None)
    assert sl_tp is None

# --- Tests for check_signal ---

@pytest.mark.asyncio
async def test_check_signal_no_signal_on_flat_market(strategy_params, mock_binance_client, mock_order_book_manager):
    """Test that no signal is generated in a flat market."""
    klines = create_klines_data(100, 30, trend='none')
    mock_binance_client.get_klines.return_value = klines
    strategy = EmaTrendFollowingStrategy("test_no_signal", "BTCUSDT", strategy_params)

    signal = await strategy.check_signal(mock_order_book_manager, mock_binance_client)

    assert signal is None

@pytest.mark.asyncio
async def test_check_signal_not_enough_data(strategy_params, mock_binance_client, mock_order_book_manager):
    """Test that no signal is generated if there is not enough k-line data."""
    klines = create_klines_data(100, 5) # Not enough for slow EMA of 10
    mock_binance_client.get_klines.return_value = klines
    strategy = EmaTrendFollowingStrategy("test_not_enough_data", "BTCUSDT", strategy_params)

    signal = await strategy.check_signal(mock_order_book_manager, mock_binance_client)

    assert signal is None

@pytest.mark.asyncio
async def test_check_signal_long_signal_generated(strategy_params, mock_binance_client, mock_order_book_manager):
    """Test that a Long signal is correctly generated."""
    # Create data that simulates a golden cross
    klines = create_klines_data(100, 30, trend='down') + create_klines_data(97, 20, trend='up')
    mock_binance_client.get_klines.return_value = klines
    strategy = EmaTrendFollowingStrategy("test_long_signal", "BTCUSDT", strategy_params)

    # Manually create a scenario for a long signal
    df = pd.DataFrame(klines, columns=['time', 'open', 'high', 'low', 'close', 'volume', 'ct','qv','nt','tbv','tqv','i'])
    for col in ['open', 'high', 'low', 'close', 'volume']:
        df[col] = pd.to_numeric(df[col])
    df.ta.ema(length=strategy.fast_ema_period, append=True, col_names=(f'EMA_{strategy.fast_ema_period}',))
    df.ta.ema(length=strategy.slow_ema_period, append=True, col_names=(f'EMA_{strategy.slow_ema_period}',))
    df.ta.rsi(length=strategy.rsi_period, append=True, col_names=(f'RSI_{strategy.rsi_period}',))
    df.ta.sma(close=df['volume'], length=strategy.volume_ma_period, append=True, col_names=(f'VOLUME_MA_{strategy.volume_ma_period}',))
    df.ta.atr(length=strategy.atr_period, append=True, col_names=(f'ATR_{strategy.atr_period}',))
    df.dropna(inplace=True)

    # Force the conditions to be met on the last two candles
    # Golden Cross
    df.iat[-2, df.columns.get_loc(f'EMA_{strategy.fast_ema_period}')] = 98
    df.iat[-2, df.columns.get_loc(f'EMA_{strategy.slow_ema_period}')] = 99
    df.iat[-1, df.columns.get_loc(f'EMA_{strategy.fast_ema_period}')] = 100
    df.iat[-1, df.columns.get_loc(f'EMA_{strategy.slow_ema_period}')] = 99.5
    # Upward slope
    df.iat[-2, df.columns.get_loc(f'EMA_{strategy.slow_ema_period}')] = 99.4 # Ensure slow EMA is also trending up
    # Momentum
    df.iat[-1, df.columns.get_loc(f'RSI_{strategy.rsi_period}')] = 55
    # Volume
    df.iat[-1, df.columns.get_loc('volume')] = 200
    df.iat[-1, df.columns.get_loc(f'VOLUME_MA_{strategy.volume_ma_period}')] = 150

    signal = await strategy.check_signal(mock_order_book_manager, mock_binance_client, dataframe=df)

    assert signal is not None
    assert signal['signal_type'] == 'Long'
    assert 'entry_price' in signal
    assert 'atr' in signal

@pytest.mark.asyncio
async def test_check_signal_short_signal_generated(strategy_params, mock_binance_client, mock_order_book_manager):
    """Test that a Short signal is correctly generated."""
    # Create data that simulates a death cross
    klines = create_klines_data(100, 30, trend='up') + create_klines_data(103, 20, trend='down')
    strategy = EmaTrendFollowingStrategy("test_short_signal", "BTCUSDT", strategy_params)

    # Manually create a scenario for a short signal
    df = pd.DataFrame(klines, columns=['time', 'open', 'high', 'low', 'close', 'volume', 'ct','qv','nt','tbv','tqv','i'])
    for col in ['open', 'high', 'low', 'close', 'volume']:
        df[col] = pd.to_numeric(df[col])
    df.ta.ema(length=strategy.fast_ema_period, append=True, col_names=(f'EMA_{strategy.fast_ema_period}',))
    df.ta.ema(length=strategy.slow_ema_period, append=True, col_names=(f'EMA_{strategy.slow_ema_period}',))
    df.ta.rsi(length=strategy.rsi_period, append=True, col_names=(f'RSI_{strategy.rsi_period}',))
    df.ta.sma(close=df['volume'], length=strategy.volume_ma_period, append=True, col_names=(f'VOLUME_MA_{strategy.volume_ma_period}',))
    df.ta.atr(length=strategy.atr_period, append=True, col_names=(f'ATR_{strategy.atr_period}',))
    df.dropna(inplace=True)
    
    # Force the conditions to be met on the last two candles
    # Death Cross
    df.iat[-2, df.columns.get_loc(f'EMA_{strategy.fast_ema_period}')] = 102
    df.iat[-2, df.columns.get_loc(f'EMA_{strategy.slow_ema_period}')] = 101
    df.iat[-1, df.columns.get_loc(f'EMA_{strategy.fast_ema_period}')] = 100
    df.iat[-1, df.columns.get_loc(f'EMA_{strategy.slow_ema_period}')] = 100.5
    # Downward slope
    df.iat[-2, df.columns.get_loc(f'EMA_{strategy.slow_ema_period}')] = 100.6 # Ensure slow EMA is also trending down
    # Momentum
    df.iat[-1, df.columns.get_loc(f'RSI_{strategy.rsi_period}')] = 45
    # Volume
    df.iat[-1, df.columns.get_loc('volume')] = 200
    df.iat[-1, df.columns.get_loc(f'VOLUME_MA_{strategy.volume_ma_period}')] = 150

    signal = await strategy.check_signal(mock_order_book_manager, mock_binance_client, dataframe=df)

    assert signal is not None
    assert signal['signal_type'] == 'Short'
    assert 'entry_price' in signal
    assert 'atr' in signal

