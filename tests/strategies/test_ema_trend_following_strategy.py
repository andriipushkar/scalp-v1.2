
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
        'kline_limit': 15,
        'adx_period': 14,
        'adx_threshold': 25,
        'use_adx_filter': True
    }

@pytest.fixture
def mock_binance_client():
    """Creates a mock for BinanceClient."""
    mock = MagicMock()
    mock.get_klines = AsyncMock()
    mock.client = MagicMock()
    mock.client.futures_klines = AsyncMock(return_value=create_klines_data(100, 60)) # Default klines for check_signal
    return mock

@pytest.fixture
def mock_order_book_manager():
    """Creates a mock for OrderBookManager."""
    return MagicMock()

# --- Helper function to create k-line data ---

def create_klines_data(base_price, count=200, trend='none'):
    """Generates sample k-line data for testing."""
    klines_list = []
    price = base_price
    for i in range(count):
        if trend == 'up':
            price += 0.1
        elif trend == 'down':
            price -= 0.1
        
        open_p = price
        high_p = price + 0.05
        low_p = price - 0.05
        close_p = price
        volume = 100 + i * 5
        
    klines_list.append([float(0), float(open_p), float(high_p), float(low_p), float(close_p), float(volume), float(0), float(0), float(0), float(0), float(0), float(0)])
    
    df = pd.DataFrame(klines_list, columns=['open_time', 'open', 'high', 'low', 'close', 'volume', 'close_time',
                                           'quote_asset_volume', 'number_of_trades', 'taker_buy_base_asset_volume',
                                           'taker_buy_quote_asset_volume', 'ignore'])
    for col in ['open', 'high', 'low', 'close', 'volume']:
        df[col] = pd.to_numeric(df[col])
    return df

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

def test_calculate_sl_tp_long_with_max_sl_percentage(strategy_params, mock_order_book_manager):
    """Test that SL for a Long signal is capped by max_sl_percentage."""
    strategy_params['max_sl_percentage'] = 0.02  # 2%
    strategy = EmaTrendFollowingStrategy("test_long_max_sl", "BTCUSDT", strategy_params)
    entry_price = 100.0
    atr = 3.0  # This would make SL = 100 - 1.5 * 3 = 95.5 (4.5%), which is > 2%
    tick_size = 0.01

    sl_tp = strategy.calculate_sl_tp(entry_price, 'Long', mock_order_book_manager, tick_size, atr=atr)

    expected_max_sl_price = entry_price * (1 - strategy.max_sl_percentage) # 100 * 0.98 = 98
    
    assert sl_tp is not None
    assert pytest.approx(sl_tp['stop_loss']) == round(expected_max_sl_price / tick_size) * tick_size

def test_calculate_sl_tp_short_with_max_sl_percentage(strategy_params, mock_order_book_manager):
    """Test that SL for a Short signal is capped by max_sl_percentage."""
    strategy_params['max_sl_percentage'] = 0.02  # 2%
    strategy = EmaTrendFollowingStrategy("test_short_max_sl", "BTCUSDT", strategy_params)
    entry_price = 100.0
    atr = 3.0  # This would make SL = 100 + 1.5 * 3 = 104.5 (4.5%), which is > 2%
    tick_size = 0.01

    sl_tp = strategy.calculate_sl_tp(entry_price, 'Short', mock_order_book_manager, tick_size, atr=atr)

    expected_max_sl_price = entry_price * (1 + strategy.max_sl_percentage) # 100 * 1.02 = 102

    assert sl_tp is not None
    assert pytest.approx(sl_tp['stop_loss']) == round(expected_max_sl_price / tick_size) * tick_size
    
# --- Tests for check_signal ---

@pytest.mark.asyncio
async def test_check_signal_no_signal_on_flat_market(strategy_params, mock_binance_client, mock_order_book_manager):
    """Test that no signal is generated in a flat market."""
    klines = create_klines_data(100, count=200, trend='none')
    strategy = EmaTrendFollowingStrategy("test_no_signal", "BTCUSDT", strategy_params)

    signal = await strategy.check_signal(mock_order_book_manager, mock_binance_client, dataframe=klines)

    assert signal is None

@pytest.mark.asyncio
async def test_check_signal_not_enough_data(strategy_params, mock_binance_client, mock_order_book_manager):
    """Test that no signal is generated if there is not enough k-line data."""
    klines = create_klines_data(100, count=5) # Not enough for slow EMA of 10
    strategy = EmaTrendFollowingStrategy("test_not_enough_data", "BTCUSDT", strategy_params)

    signal = await strategy.check_signal(mock_order_book_manager, mock_binance_client, dataframe=klines)

    assert signal is None

@pytest.mark.asyncio
async def test_check_signal_long_signal_generated(strategy_params, mock_binance_client, mock_order_book_manager):
    """Test that a Long signal is correctly generated."""
    # Create data that simulates a golden cross
    df = pd.concat([create_klines_data(100, count=100, trend='down'), create_klines_data(97, count=100, trend='up')], ignore_index=True)
    mock_binance_client.client.futures_klines.return_value = df.values.tolist() # Mock klines for internal fetching
    strategy = EmaTrendFollowingStrategy("test_long_signal", "BTCUSDT", strategy_params)

    df[f'EMA_{strategy.fast_ema_period}'] = df.ta.ema(length=strategy.fast_ema_period)[f'EMA_{strategy.fast_ema_period}']
    df[f'EMA_{strategy.slow_ema_period}'] = df.ta.ema(length=strategy.slow_ema_period)[f'EMA_{strategy.slow_ema_period}']
    df[f'RSI_{strategy.rsi_period}'] = df.ta.rsi(length=strategy.rsi_period)[f'RSI_{strategy.rsi_period}']
    df[f'VOLUME_MA_{strategy.volume_ma_period}'] = df.ta.sma(close=df['volume'], length=strategy.volume_ma_period)[f'SMA_{strategy.volume_ma_period}']
    df[f'ATR_{strategy.atr_period}'] = df.ta.atr(length=strategy.atr_period)[f'ATR_{strategy.atr_period}']
    adx_data = df.ta.adx(length=strategy.adx_period)
    df[f'ADX_{strategy.adx_period}'] = adx_data[f'ADX_{strategy.adx_period}']
    df[f'DMP_{strategy.adx_period}'] = adx_data[f'DMP_{strategy.adx_period}']
    df[f'DMN_{strategy.adx_period}'] = adx_data[f'DMN_{strategy.adx_period}']
    df.dropna(inplace=True)

    # Force the conditions to be met on the last two candles
    # Golden Cross
    df[f'EMA_{strategy.fast_ema_period}'].iloc[-2] = 98
    df[f'EMA_{strategy.slow_ema_period}'].iloc[-2] = 99
    df[f'EMA_{strategy.fast_ema_period}'].iloc[-1] = 100
    df[f'EMA_{strategy.slow_ema_period}'].iloc[-1] = 99.5
    # Upward slope
    df[f'EMA_{strategy.slow_ema_period}'].iloc[-2] = 99.4 # Ensure slow EMA is also trending up
    # Momentum
    df[f'RSI_{strategy.rsi_period}'].iloc[-1] = 55
    # Volume
    df['volume'].iloc[-1] = 200
    df[f'VOLUME_MA_{strategy.volume_ma_period}'].iloc[-1] = 150
    # ADX
    df[f'ADX_{strategy.adx_period}'].iloc[-1] = 30 # Above threshold

    signal = await strategy.check_signal(mock_order_book_manager, mock_binance_client, dataframe=df)

    assert signal is not None
    assert signal['signal_type'] == 'Long'
    assert 'entry_price' in signal
    assert 'atr' in signal

@pytest.mark.asyncio
async def test_check_signal_short_signal_generated(strategy_params, mock_binance_client, mock_order_book_manager):
    """Test that a Short signal is correctly generated."""
    # Create data that simulates a death cross
    df = pd.concat([create_klines_data(100, count=100, trend='up'), create_klines_data(103, count=100, trend='down')], ignore_index=True)
    mock_binance_client.client.futures_klines.return_value = df.values.tolist() # Mock klines for internal fetching
    strategy = EmaTrendFollowingStrategy("test_short_signal", "BTCUSDT", strategy_params)

    df[f'EMA_{strategy.fast_ema_period}'] = df.ta.ema(length=strategy.fast_ema_period)[f'EMA_{strategy.fast_ema_period}']
    df[f'EMA_{strategy.slow_ema_period}'] = df.ta.ema(length=strategy.slow_ema_period)[f'EMA_{strategy.slow_ema_period}']
    df[f'RSI_{strategy.rsi_period}'] = df.ta.rsi(length=strategy.rsi_period)[f'RSI_{strategy.rsi_period}']
    df[f'VOLUME_MA_{strategy.volume_ma_period}'] = df.ta.sma(close=df['volume'], length=strategy.volume_ma_period)[f'SMA_{strategy.volume_ma_period}']
    df[f'ATR_{strategy.atr_period}'] = df.ta.atr(length=strategy.atr_period)[f'ATR_{strategy.atr_period}']
    adx_data = df.ta.adx(length=strategy.adx_period)
    df[f'ADX_{strategy.adx_period}'] = adx_data[f'ADX_{strategy.adx_period}']
    df[f'DMP_{strategy.adx_period}'] = adx_data[f'DMP_{strategy.adx_period}']
    df[f'DMN_{strategy.adx_period}'] = adx_data[f'DMN_{strategy.adx_period}']
    df.dropna(inplace=True)
    
    # Force the conditions to be met on the last two candles
    # Death Cross
    df[f'EMA_{strategy.fast_ema_period}'].iloc[-2] = 102
    df[f'EMA_{strategy.slow_ema_period}'].iloc[-2] = 101
    df[f'EMA_{strategy.fast_ema_period}'].iloc[-1] = 100
    df[f'EMA_{strategy.slow_ema_period}'].iloc[-1] = 100.5
    # Downward slope
    df[f'EMA_{strategy.slow_ema_period}'].iloc[-2] = 100.6 # Ensure slow EMA is also trending down
    # Momentum
    df[f'RSI_{strategy.rsi_period}'].iloc[-1] = 45
    # Volume
    df['volume'].iloc[-1] = 200
    df[f'VOLUME_MA_{strategy.volume_ma_period}'].iloc[-1] = 150
    # ADX
    df[f'ADX_{strategy.adx_period}'].iloc[-1] = 30 # Above threshold

    signal = await strategy.check_signal(mock_order_book_manager, mock_binance_client, dataframe=df)

    assert signal is not None
    assert signal['signal_type'] == 'Short'
    assert 'entry_price' in signal
    assert 'atr' in signal

@pytest.mark.asyncio
async def test_check_signal_adx_filter_active_no_signal_low_adx(strategy_params, mock_binance_client, mock_order_book_manager):
    """Test that no signal is generated when ADX is below the threshold."""
    strategy_params['adx_threshold'] = 25
    strategy_params['use_adx_filter'] = True
    strategy = EmaTrendFollowingStrategy("test_adx_low", "BTCUSDT", strategy_params)

    df = create_klines_data(100, count=200, trend='none') # Enough data for ADX
    mock_binance_client.client.futures_klines.return_value = df.values.tolist() # Mock klines for internal fetching

    for col in ['open', 'high', 'low', 'close', 'volume']:
        df[col] = pd.to_numeric(df[col])
    df[f'EMA_{strategy.fast_ema_period}'] = df.ta.ema(length=strategy.fast_ema_period)
    df[f'EMA_{strategy.slow_ema_period}'] = df.ta.ema(length=strategy.slow_ema_period)
    df[f'RSI_{strategy.rsi_period}'] = df.ta.rsi(length=strategy.rsi_period)
    df[f'VOLUME_MA_{strategy.volume_ma_period}'] = df.ta.sma(close=df['volume'], length=strategy.volume_ma_period)
    df[f'ATR_{strategy.atr_period}'] = df.ta.atr(length=strategy.atr_period)
    adx_data = df.ta.adx(length=strategy.adx_period)
    df[f'ADX_{strategy.adx_period}'] = adx_data[f'ADX_{strategy.adx_period}']
    df[f'DMP_{strategy.adx_period}'] = adx_data[f'DMP_{strategy.adx_period}']
    df[f'DMN_{strategy.adx_period}'] = adx_data[f'DMN_{strategy.adx_period}']
    df.dropna(inplace=True)

    # Force signal conditions
    df[f'EMA_{strategy.fast_ema_period}'].iloc[-2] = 98
    df[f'EMA_{strategy.slow_ema_period}'].iloc[-2] = 99
    df[f'EMA_{strategy.fast_ema_period}'].iloc[-1] = 100
    df[f'EMA_{strategy.slow_ema_period}'].iloc[-1] = 99.5
    df[f'EMA_{strategy.slow_ema_period}'].iloc[-2] = 99.4
    df[f'RSI_{strategy.rsi_period}'].iloc[-1] = 55
    df['volume'].iloc[-1] = 200
    df[f'VOLUME_MA_{strategy.volume_ma_period}'].iloc[-1] = 150
    
    # Force low ADX
    df[f'ADX_{strategy.adx_period}'].iloc[-1] = 20 # Below threshold

    signal = await strategy.check_signal(mock_order_book_manager, mock_binance_client, dataframe=df)

    assert signal is None
@pytest.mark.asyncio
async def test_check_signal_adx_filter_active_signal_high_adx(strategy_params, mock_binance_client, mock_order_book_manager):
    """Test that a Long signal is generated when ADX is above the threshold."""
    strategy_params['adx_threshold'] = 25
    strategy_params['use_adx_filter'] = True
    strategy = EmaTrendFollowingStrategy("test_adx_high", "BTCUSDT", strategy_params)

    # Create data that would normally generate a LONG signal with high ADX
    df = create_klines_data(100, count=200, trend='none') # Enough data for ADX
    mock_binance_client.client.futures_klines.return_value = df.values.tolist() # Mock klines for internal fetching

    for col in ['open', 'high', 'low', 'close', 'volume']:
        df[col] = pd.to_numeric(df[col])
    df[f'EMA_{strategy.fast_ema_period}'] = df.ta.ema(length=strategy.fast_ema_period)
    df[f'EMA_{strategy.slow_ema_period}'] = df.ta.ema(length=strategy.slow_ema_period)
    df[f'RSI_{strategy.rsi_period}'] = df.ta.rsi(length=strategy.rsi_period)
    df[f'VOLUME_MA_{strategy.volume_ma_period}'] = df.ta.sma(close=df['volume'], length=strategy.volume_ma_period)
    df[f'ATR_{strategy.atr_period}'] = df.ta.atr(length=strategy.atr_period)
    adx_data = df.ta.adx(length=strategy.adx_period)
    df[f'ADX_{strategy.adx_period}'] = adx_data[f'ADX_{strategy.adx_period}']
    df[f'DMP_{strategy.adx_period}'] = adx_data[f'DMP_{strategy.adx_period}']
    df[f'DMN_{strategy.adx_period}'] = adx_data[f'DMN_{strategy.adx_period}']
    df.dropna(inplace=True)

    # Force signal conditions
    df[f'EMA_{strategy.fast_ema_period}'].iloc[-2] = 98
    df[f'EMA_{strategy.slow_ema_period}'].iloc[-2] = 99
    df[f'EMA_{strategy.fast_ema_period}'].iloc[-1] = 100
    df[f'EMA_{strategy.slow_ema_period}'].iloc[-1] = 99.5
    df[f'EMA_{strategy.slow_ema_period}'].iloc[-2] = 99.4
    df[f'RSI_{strategy.rsi_period}'].iloc[-1] = 55
    df['volume'].iloc[-1] = 200
    df[f'VOLUME_MA_{strategy.volume_ma_period}'].iloc[-1] = 150
    
    # Force high ADX
    df[f'ADX_{strategy.adx_period}'].iloc[-1] = 30 # Above threshold

    signal = await strategy.check_signal(mock_order_book_manager, mock_binance_client, dataframe=df)

    assert signal is not None
    assert signal['signal_type'] == 'Long'
    assert 'entry_price' in signal
    assert 'atr' in signal

