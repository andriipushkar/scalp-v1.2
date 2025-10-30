
import pytest
import pandas as pd
from unittest.mock import MagicMock, AsyncMock

from strategies.macd_trend_filter_strategy import MacdTrendFilterStrategy

# --- Fixtures ---

@pytest.fixture
def strategy_params():
    """Provides a default set of parameters for the strategy."""
    return {
        'macd_fast': 12,
        'macd_slow': 26,
        'macd_signal': 9,
        'ema_trend_period': 200,
        'atr_period': 14,
        'sl_atr_multiplier': 1.5,
        'rr_ratio': 2.0,
        'kline_interval': '15m',
        'max_sl_percentage': 0.05, # 5%
        'use_breakeven_sl': True,
    }

@pytest.fixture
def mock_binance_client():
    """Creates a mock for BinanceClient."""
    mock = MagicMock()
    mock.client = MagicMock()
    mock.client.futures_klines = AsyncMock()
    return mock

@pytest.fixture
def mock_order_book_manager():
    """Creates a mock for OrderBookManager."""
    mock = MagicMock()
    mock.get_current_price.return_value = 105.0 # Default current price
    mock.get_tick_size.return_value = 0.01
    return mock

# --- Helper function to create k-line data ---

def create_test_dataframe(strategy_params: dict, trend='none', noise=0.1):
    params = strategy_params
    count = params['ema_trend_period'] + 50
    base_price = 100
    
    klines_list = []
    price = base_price
    for i in range(count):
        if trend == 'up':
            price += 0.1
        elif trend == 'down':
            price -= 0.1
        
        open_p = price
        close_p = price
        high_p = price
        low_p = price

        if noise > 0:
            high_p = price + noise
            low_p = price - noise
            close_p = price + (noise / 2 * (1 if trend == 'up' else -1))

        klines_list.append([
            pd.Timestamp.now().timestamp() * 1000,
            float(open_p), float(high_p), float(low_p), float(close_p),
            float(100 + i), pd.Timestamp.now().timestamp() * 1000 + 60000,
            float(10000 + i * 100), 10, float(50 + i), float(5000 + i * 50), 0
        ])
        
    df = pd.DataFrame(klines_list, columns=[
        'open_time', 'open', 'high', 'low', 'close', 'volume', 'close_time',
        'quote_asset_volume', 'number_of_trades', 'taker_buy_base_asset_volume',
        'taker_buy_quote_asset_volume', 'ignore'
    ])
    for col in ['open', 'high', 'low', 'close', 'volume']:
        df[col] = pd.to_numeric(df[col])

    # Calculate indicators
    df.ta.macd(fast=params['macd_fast'], slow=params['macd_slow'], signal=params['macd_signal'], append=True)
    df.ta.ema(length=params['ema_trend_period'], append=True, col_names=(f'EMA_{params["ema_trend_period"]}',))
    df.ta.atr(length=params['atr_period'], append=True, col_names=(f'ATR_{params["atr_period"]}',))

    return df

# --- Tests for calculate_sl_tp ---

def test_calculate_sl_tp_long(strategy_params, mock_order_book_manager):
    strategy = MacdTrendFilterStrategy("test_sl_long", "BTCUSDT", strategy_params)
    entry_price = 100.0
    atr = 2.0
    tick_size = 0.01
    sl_tp = strategy.calculate_sl_tp(entry_price, 'Long', mock_order_book_manager, tick_size, atr=atr)
    expected_sl = 100.0 - (1.5 * 2.0) # 97.0
    assert sl_tp is not None
    assert sl_tp['stop_loss'] == expected_sl
    assert sl_tp['take_profit'] is None

def test_calculate_sl_tp_short(strategy_params, mock_order_book_manager):
    strategy = MacdTrendFilterStrategy("test_sl_short", "BTCUSDT", strategy_params)
    entry_price = 100.0
    atr = 2.0
    tick_size = 0.01
    sl_tp = strategy.calculate_sl_tp(entry_price, 'Short', mock_order_book_manager, tick_size, atr=atr)
    expected_sl = 100.0 + (1.5 * 2.0) # 103.0
    assert sl_tp is not None
    assert sl_tp['stop_loss'] == expected_sl
    assert sl_tp['take_profit'] is None

# --- Tests for check_signal ---

@pytest.mark.asyncio
async def test_check_signal_no_signal_on_flat_market(strategy_params, mock_binance_client, mock_order_book_manager):
    df = create_test_dataframe(strategy_params=strategy_params, trend='none')
    strategy = MacdTrendFilterStrategy("test_no_signal", "BTCUSDT", strategy_params)
    signal = await strategy.check_signal(mock_order_book_manager, mock_binance_client, dataframe=df.copy())
    assert signal is None

@pytest.mark.asyncio
async def test_check_signal_long_signal(strategy_params, mock_binance_client, mock_order_book_manager):
    df = create_test_dataframe(strategy_params=strategy_params, trend='up')
    strategy = MacdTrendFilterStrategy("test_long_signal", "BTCUSDT", strategy_params)
    
    df.dropna(inplace=True)
    df.reset_index(drop=True, inplace=True)

    # Force MACD cross up
    df.loc[df.index[-2], f'MACD_{strategy.macd_fast}_{strategy.macd_slow}_{strategy.macd_signal}'] = 0.1
    df.loc[df.index[-2], f'MACDs_{strategy.macd_fast}_{strategy.macd_slow}_{strategy.macd_signal}'] = 0.2
    df.loc[df.index[-1], f'MACD_{strategy.macd_fast}_{strategy.macd_slow}_{strategy.macd_signal}'] = 0.3
    df.loc[df.index[-1], f'MACDs_{strategy.macd_fast}_{strategy.macd_slow}_{strategy.macd_signal}'] = 0.25
    
    signal = await strategy.check_signal(mock_order_book_manager, mock_binance_client, dataframe=df.copy())
    
    assert signal is not None
    assert signal['signal_type'] == 'Long'

@pytest.mark.asyncio
async def test_check_signal_short_signal(strategy_params, mock_binance_client, mock_order_book_manager):
    df = create_test_dataframe(strategy_params=strategy_params, trend='down')
    strategy = MacdTrendFilterStrategy("test_short_signal", "BTCUSDT", strategy_params)

    df.dropna(inplace=True)
    df.reset_index(drop=True, inplace=True)

    # Force MACD cross down
    df.loc[df.index[-2], f'MACD_{strategy.macd_fast}_{strategy.macd_slow}_{strategy.macd_signal}'] = 0.2
    df.loc[df.index[-2], f'MACDs_{strategy.macd_fast}_{strategy.macd_slow}_{strategy.macd_signal}'] = 0.1
    df.loc[df.index[-1], f'MACD_{strategy.macd_fast}_{strategy.macd_slow}_{strategy.macd_signal}'] = 0.1
    df.loc[df.index[-1], f'MACDs_{strategy.macd_fast}_{strategy.macd_slow}_{strategy.macd_signal}'] = 0.15

    # Force price below EMA trend
    df.loc[df.index[-1], 'close'] = df.loc[df.index[-1], f'EMA_{strategy.ema_trend_period}'] - 1

    signal = await strategy.check_signal(mock_order_book_manager, mock_binance_client, dataframe=df.copy())
    
    assert signal is not None
    assert signal['signal_type'] == 'Short'

# --- Tests for analyze_and_adjust ---

@pytest.mark.asyncio
async def test_analyze_and_adjust_breakeven_long(strategy_params, mock_binance_client, mock_order_book_manager):
    strategy = MacdTrendFilterStrategy("test_adjust_be_long", "BTCUSDT", strategy_params)
    position = {
        'entry_price': 100.0,
        'initial_stop_loss': 98.0,
        'stop_loss': 98.0,
        'side': 'Long',
    }
    # Price moves 1:1 R:R
    mock_order_book_manager.get_current_price.return_value = 102.0 
    
    df = create_test_dataframe(strategy_params=strategy_params, noise=0)
    
    adjustment = await strategy.analyze_and_adjust(position, mock_order_book_manager, mock_binance_client, dataframe=df)
    
    assert adjustment is not None
    assert adjustment['command'] == 'UPDATE_STOP_LOSS'
    assert adjustment['new_stop_loss'] == 100.0 # Breakeven

@pytest.mark.asyncio
async def test_analyze_and_adjust_trailing_stop_long(strategy_params, mock_binance_client, mock_order_book_manager):
    strategy = MacdTrendFilterStrategy("test_adjust_ts_long", "BTCUSDT", strategy_params)
    position = {
        'entry_price': 100.0,
        'initial_stop_loss': 98.0,
        'stop_loss': 100.0, # Already at breakeven
        'side': 'Long',
    }
    mock_order_book_manager.get_current_price.return_value = 110.0
    
    df = create_test_dataframe(strategy_params=strategy_params)
    df.loc[df.index[-1], f'ATR_{strategy_params["atr_period"]}'] = 2.0
    
    adjustment = await strategy.analyze_and_adjust(position, mock_order_book_manager, mock_binance_client, dataframe=df)
    
    expected_sl = 110.0 - (1.5 * 2.0) # 107.0
    
    assert adjustment is not None
    assert adjustment['command'] == 'UPDATE_STOP_LOSS'
    assert pytest.approx(adjustment['new_stop_loss']) == round(expected_sl / 0.01) * 0.01
