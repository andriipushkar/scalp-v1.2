import pytest
import pandas as pd
from unittest.mock import AsyncMock, MagicMock, patch
import asyncio

from core.trade_executor import TradeExecutor
from strategies.base_strategy import BaseStrategy
from binance.enums import ORDER_TYPE_LIMIT, ORDER_TYPE_MARKET, SIDE_BUY, SIDE_SELL, TIME_IN_FORCE_GTC

# Позначаємо всі тести в цьому файлі як асинхронні
pytestmark = pytest.mark.asyncio

# --- Фікстури для моків ---

@pytest.fixture
def mock_binance_client():
    """Мок для BinanceClient."""
    client = AsyncMock()
    client.get_account_balance.return_value = 1000.0
    return client

@pytest.fixture
def mock_position_manager():
    """Мок для PositionManager."""
    pm = MagicMock()
    pm.get_position_by_symbol.return_value = None
    pm.get_positions_count.return_value = 0
    return pm

@pytest.fixture
def mock_orderbook_manager():
    """Мок для OrderBookManager."""
    obm = MagicMock()
    obm.is_initialized = True
    obm.update_queue = asyncio.Queue()
    obm.get_best_ask.return_value = 100.1
    return obm

@pytest.fixture
def mock_orchestrator():
    """Мок для BotOrchestrator."""
    orch = MagicMock()
    orch.trading_config = {'margin_per_trade_pct': 0.1}
    orch.pending_sl_tp = {}
    return orch

@pytest.fixture
def mock_strategy():
    """Мок для екземпляру стратегії."""
    strategy = MagicMock(spec=BaseStrategy)
    strategy.strategy_id = "TestStrategy_BTCUSDT"
    strategy.symbol = "BTCUSDT"
    strategy.params = {
        "entry_order_type": ORDER_TYPE_LIMIT,
        "entry_offset_ticks": 50
    }
    return strategy

@pytest.fixture
def trade_executor(mock_strategy, mock_binance_client, mock_position_manager, mock_orchestrator, mock_orderbook_manager):
    """Фікстура для створення TradeExecutor з усіма моками."""
    return TradeExecutor(
        strategy=mock_strategy,
        binance_client=mock_binance_client,
        position_manager=mock_position_manager,
        orchestrator=mock_orchestrator,
        orderbook_manager=mock_orderbook_manager,
        max_active_trades=5,
        leverage=10,
        price_precision=2,
        qty_precision=3,
        tick_size=0.01,
        pending_symbols=set()
    )

# --- Тести --- 

async def test_check_and_open_position_pending_symbol(trade_executor: TradeExecutor):
    """ТЕСТ: Не повинно бути дій, якщо символ вже в очікуванні."""
    trade_executor.pending_symbols.add("BTCUSDT")
    await trade_executor._check_and_open_position()
    trade_executor.strategy.check_signal.assert_not_called()

async def test_check_and_open_position_max_trades_reached(trade_executor: TradeExecutor, mock_position_manager):
    """ТЕСТ: Не повинно бути дій, якщо досягнуто ліміту угод."""
    mock_position_manager.get_positions_count.return_value = trade_executor.max_active_trades
    await trade_executor._check_and_open_position()
    trade_executor.strategy.check_signal.assert_not_called()

async def test_check_and_open_position_signal_found(trade_executor: TradeExecutor):
    """ТЕСТ: Якщо є сигнал, має викликатись _open_position."""
    trade_executor.strategy.check_signal.return_value = {"signal_type": "Long", "wall_price": 100.0}
    trade_executor._open_position = AsyncMock() # Мокаємо внутрішній виклик
    
    await trade_executor._check_and_open_position()
    
    trade_executor.strategy.check_signal.assert_called_once()
    trade_executor._open_position.assert_called_once_with({"signal_type": "Long", "wall_price": 100.0})

async def test_open_position_limit_order(trade_executor: TradeExecutor, mock_binance_client, mock_orchestrator):
    """ТЕСТ: Коректне виставлення лімітного ордеру."""
    signal = {"signal_type": "Short", "wall_price": 100.0}
    
    await trade_executor._open_position(signal)
    
    mock_binance_client.futures_create_order.assert_called_once()
    args, kwargs = mock_binance_client.futures_create_order.call_args
    
    assert kwargs['symbol'] == "BTCUSDT"
    assert kwargs['side'] == SIDE_SELL
    assert kwargs['type'] == ORDER_TYPE_LIMIT
    assert kwargs['price'] == "99.50" # 100.0 - (50 * 0.01)
    assert kwargs['quantity'] == pytest.approx(10.05) # (1000 * 0.1 * 10) / 99.5 = 10.05025 -> floor to 10.050
    assert kwargs['timeInForce'] == TIME_IN_FORCE_GTC
    assert "newClientOrderId" in kwargs
    assert kwargs['newClientOrderId'] in mock_orchestrator.pending_sl_tp

async def test_open_position_market_order(trade_executor: TradeExecutor, mock_binance_client, mock_orchestrator):
    """ТЕСТ: Коректне виставлення ринкового ордеру."""
    trade_executor.strategy.params["entry_order_type"] = ORDER_TYPE_MARKET
    signal = {"signal_type": "Long", "wall_price": 100.0}
    
    await trade_executor._open_position(signal)
    
    mock_binance_client.futures_create_order.assert_called_once()
    args, kwargs = mock_binance_client.futures_create_order.call_args
    
    assert kwargs['type'] == ORDER_TYPE_MARKET
    assert kwargs['quantity'] == pytest.approx(9.99) # (1000 * 0.1 * 10) / 100.1

async def test_handle_position_adjustment_close_position(trade_executor: TradeExecutor, mock_position_manager, mock_binance_client):
    """ТЕСТ: Коректне завчасне закриття позиції."""
    position = {
        "symbol": "BTCUSDT", "side": "Long", "quantity": 0.01,
        "sl_order_id": 123, "tp_order_id": 456
    }
    trade_executor.strategy.analyze_and_adjust.return_value = {"command": "CLOSE_POSITION"}
    mock_position_manager.get_position_by_symbol.return_value = position
    
    await trade_executor._handle_position_adjustment(position)
    
    trade_executor.strategy.analyze_and_adjust.assert_called_once()
    # Перевіряємо, що були спроби скасувати старі SL/TP
    assert mock_binance_client.cancel_all_open_orders.called
    # Перевіряємо виставлення ордеру на закриття
    mock_binance_client.futures_create_order.assert_called_once_with(
        symbol="BTCUSDT", side=SIDE_SELL, type=ORDER_TYPE_MARKET, quantity=0.01, reduceOnly=True
    )

async def test_handle_position_adjustment_adjust_tp_sl(trade_executor: TradeExecutor, mock_position_manager, mock_binance_client):
    """ТЕСТ: Коректне коригування SL/TP."""
    position = {
        "symbol": "BTCUSDT", "side": "Long", "quantity": 0.01,
        "sl_order_id": 123, "tp_order_id": 456
    }
    trade_executor.strategy.analyze_and_adjust.return_value = {
        "command": "ADJUST_TP_SL", "stop_loss": 99.5, "take_profit": 101.5
    }
    # Мокаємо повернення ID для нових ордерів
    mock_binance_client.create_stop_market_order.return_value = {"orderId": 789}
    mock_binance_client.create_take_profit_market_order.return_value = {"orderId": 987}

    await trade_executor._handle_position_adjustment(position)

    # Перевіряємо скасування старих ордерів
    mock_binance_client.cancel_order.assert_any_call("BTCUSDT", 123)
    mock_binance_client.cancel_order.assert_any_call("BTCUSDT", 456)
    
    # Перевіряємо створення нових ордерів
    mock_binance_client.create_stop_market_order.assert_called_once_with(
        "BTCUSDT", SIDE_SELL, 0.01, 99.5, 2, 3
    )
    mock_binance_client.create_take_profit_market_order.assert_called_once_with(
        "BTCUSDT", SIDE_SELL, 0.01, 101.5, 2, 3
    )
    
    # Перевіряємо оновлення ID в менеджері позицій
    mock_position_manager.update_orders.assert_called_once_with("BTCUSDT", sl_order_id=789, tp_order_id=987)