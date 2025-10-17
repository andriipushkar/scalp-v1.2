import pytest
import pandas as pd
from unittest.mock import AsyncMock, MagicMock, patch
import asyncio

from core.trade_executor import TradeExecutor
from strategies.liquidity_hunting_strategy import LiquidityHuntingStrategy
from strategies.dynamic_orderbook_strategy import DynamicOrderbookStrategy
from binance.enums import ORDER_TYPE_LIMIT, ORDER_TYPE_MARKET, SIDE_BUY, SIDE_SELL, TIME_IN_FORCE_GTC

# --- Фікстури для моків ---

@pytest.fixture
def mock_binance_client():
    client = AsyncMock()
    client.get_symbol_info.return_value = {
        'filters': [
            {'filterType': 'PRICE_FILTER', 'tickSize': '0.01'},
            {'filterType': 'LOT_SIZE', 'stepSize': '0.001'}
        ],
        'pricePrecision': 2,
        'quantityPrecision': 3
    }
    client.get_account_balance.return_value = 1000.0
    return client

@pytest.fixture
def mock_position_manager():
    pm = MagicMock()
    pm.get_position_by_symbol.return_value = None
    pm.get_positions_count.return_value = 0
    return pm

@pytest.fixture
def mock_orderbook_manager():
    obm = MagicMock()
    obm.is_initialized = True
    obm.update_queue = asyncio.Queue() # Для імітації очікування оновлень
    obm.get_best_bid.return_value = 100.0
    obm.get_best_ask.return_value = 100.1
    obm.get_bids.return_value = pd.DataFrame({'quantity': [100]}, index=pd.Index([100.0], name='price'))
    obm.get_asks.return_value = pd.DataFrame({'quantity': [100]}, index=pd.Index([100.1], name='price'))
    return obm

@pytest.fixture
def mock_orchestrator():
    orch = MagicMock()
    orch.trading_config = {'margin_per_trade_pct': 0.1, 'leverage': 10, 'max_active_trades': 5}
    orch.pending_sl_tp = {}
    return orch

@pytest.fixture
def pending_symbols_set():
    return set()

@pytest.fixture
def default_trade_executor(mock_binance_client, mock_position_manager, mock_orchestrator, mock_orderbook_manager, pending_symbols_set):
    strategy_config = {
        "strategy_id": "TestStrategy_BTCUSDT",
        "symbol": "BTCUSDT",
        "strategy_name": "LiquidityHunting",
        "params": {
            "wall_volume_multiplier": 10,
            "activation_distance_ticks": 15,
            "stop_loss_pct": 0.005,
            "risk_reward_ratio": 1.5,
            "tp_offset_ticks": 10
        }
    }
    executor = TradeExecutor(
        strategy_config=strategy_config,
        binance_client=mock_binance_client,
        position_manager=mock_position_manager,
        orchestrator=mock_orchestrator,
        orderbook_manager=mock_orderbook_manager,
        max_active_trades=5,
        leverage=10,
        price_precision=2,
        qty_precision=3,
        tick_size=0.01,
        pending_symbols=pending_symbols_set
    )
    executor.strategy = MagicMock(spec=LiquidityHuntingStrategy) # Mock the strategy
    executor.strategy.strategy_id = strategy_config["strategy_id"]
    executor.strategy.params = strategy_config["params"]
    return executor

@pytest.fixture
def dynamic_trade_executor(mock_binance_client, mock_position_manager, mock_orchestrator, mock_orderbook_manager, pending_symbols_set):
    strategy_config = {
        "strategy_id": "DynamicBTC",
        "symbol": "BTCUSDT",
        "strategy_name": "DynamicOrderbook",
        "params": {
            "entry_order_type": "MARKET",
            "stop_loss_percent": 1.0,
            "initial_tp_search_percent": 2.0,
            "trailing_sl_distance_percent": 0.5,
            "pre_emptive_close_threshold_mult": 2.0,
            "wall_volume_multiplier": 10,
            "activation_distance_ticks": 15
        }
    }
    executor = TradeExecutor(
        strategy_config=strategy_config,
        binance_client=mock_binance_client,
        position_manager=mock_position_manager,
        orchestrator=mock_orchestrator,
        orderbook_manager=mock_orderbook_manager,
        max_active_trades=5,
        leverage=10,
        price_precision=2,
        qty_precision=3,
        tick_size=0.01,
        pending_symbols=pending_symbols_set
    )
    executor.strategy = MagicMock(spec=DynamicOrderbookStrategy) # Mock the dynamic strategy
    executor.strategy.strategy_id = strategy_config["strategy_id"]
    executor.strategy.params = strategy_config["params"]
    return executor

# --- Тести для _initialize_strategy ---

def test_initialize_strategy_liquidity_hunting(default_trade_executor):
    assert isinstance(default_trade_executor.strategy, LiquidityHuntingStrategy)
    assert default_trade_executor.strategy.strategy_id == "TestStrategy_BTCUSDT"

def test_initialize_strategy_dynamic_orderbook(mock_binance_client, mock_position_manager, mock_orchestrator, mock_orderbook_manager, pending_symbols_set):
    strategy_config = {
        "strategy_id": "DynamicBTC",
        "symbol": "BTCUSDT",
        "strategy_name": "DynamicOrderbook",
        "params": {
            "entry_order_type": "MARKET",
            "stop_loss_percent": 1.0,
            "initial_tp_search_percent": 2.0,
            "trailing_sl_distance_percent": 0.5,
            "pre_emptive_close_threshold_mult": 2.0,
            "wall_volume_multiplier": 10,
            "activation_distance_ticks": 15
        }
    }
    executor = TradeExecutor(
        strategy_config=strategy_config,
        binance_client=mock_binance_client,
        position_manager=mock_position_manager,
        orchestrator=mock_orchestrator,
        orderbook_manager=mock_orderbook_manager,
        max_active_trades=5,
        leverage=10,
        price_precision=2,
        qty_precision=3,
        tick_size=0.01,
        pending_symbols=pending_symbols_set
    )
    assert isinstance(executor.strategy, DynamicOrderbookStrategy)
    assert executor.strategy.strategy_id == "DynamicBTC"

def test_initialize_strategy_unknown_strategy(mock_binance_client, mock_position_manager, mock_orchestrator, mock_orderbook_manager, pending_symbols_set):
    strategy_config = {
        "strategy_id": "Unknown_BTCUSDT",
        "symbol": "BTCUSDT",
        "strategy_name": "NonExistentStrategy",
        "params": {}
    }
    with pytest.raises(ValueError, match="Невідома назва стратегії: NonExistentStrategy"):
        TradeExecutor(
            strategy_config=strategy_config,
            binance_client=mock_binance_client,
            position_manager=mock_position_manager,
            orchestrator=mock_orchestrator,
            orderbook_manager=mock_orderbook_manager,
            max_active_trades=5,
            leverage=10,
            price_precision=2,
            qty_precision=3,
            tick_size=0.01,
            pending_symbols=pending_symbols_set
        )

# --- Тести для _check_and_open_position ---

@pytest.mark.asyncio
async def test_check_and_open_position_pending_symbol(default_trade_executor, pending_symbols_set):
    pending_symbols_set.add("BTCUSDT")
    await default_trade_executor._check_and_open_position()
    default_trade_executor.strategy.check_signal.assert_not_called()

@pytest.mark.asyncio
async def test_check_and_open_position_max_trades_reached(default_trade_executor, mock_position_manager):
    """Тест перевіряє, що check_signal не викликається, якщо досягнуто ліміту активних угод."""
    mock_position_manager.get_positions_count.return_value = default_trade_executor.max_active_trades
    await default_trade_executor._check_and_open_position()
    default_trade_executor.strategy.check_signal.assert_not_called()

@pytest.mark.asyncio
async def test_check_and_open_position_signal_found(default_trade_executor):
    default_trade_executor.strategy.check_signal.return_value = {"signal_type": "Long", "wall_price": 100.0}
    default_trade_executor._open_position = AsyncMock()
    await default_trade_executor._check_and_open_position()
    default_trade_executor.strategy.check_signal.assert_called_once()
    default_trade_executor._open_position.assert_called_once_with({"signal_type": "Long", "wall_price": 100.0})

@pytest.mark.asyncio
async def test_check_and_open_position_no_signal(default_trade_executor):
    default_trade_executor.strategy.check_signal.return_value = None
    default_trade_executor._open_position = AsyncMock()
    await default_trade_executor._check_and_open_position()
    default_trade_executor.strategy.check_signal.assert_called_once()
    default_trade_executor._open_position.assert_not_called()

# --- Тести для _handle_position_adjustment ---

@pytest.mark.asyncio
async def test_handle_position_adjustment_close_position(dynamic_trade_executor, mock_position_manager, mock_binance_client):
    position = {
        "symbol": "BTCUSDT",
        "side": "Long",
        "quantity": 0.001,
        "entry_price": 100.0,
        "stop_loss": 99.0,
        "take_profit": 101.0,
        "sl_order_id": 123,
        "tp_order_id": 456
    }
    mock_position_manager.get_position_by_symbol.return_value = position
    dynamic_trade_executor.strategy.analyze_and_adjust.return_value = {"command": "CLOSE_POSITION"}

    await dynamic_trade_executor._handle_position_adjustment(position)

    dynamic_trade_executor.strategy.analyze_and_adjust.assert_called_once_with(position, dynamic_trade_executor.orderbook_manager)
    mock_binance_client.cancel_order.assert_any_call("BTCUSDT", 123)
    mock_binance_client.cancel_order.assert_any_call("BTCUSDT", 456)
    mock_binance_client.futures_create_order.assert_called_once_with(
        symbol="BTCUSDT", side=SIDE_SELL, type=ORDER_TYPE_MARKET, quantity=0.001
    )

@pytest.mark.asyncio
async def test_handle_position_adjustment_adjust_tp_sl(dynamic_trade_executor, mock_position_manager, mock_binance_client):
    position = {
        "symbol": "BTCUSDT",
        "side": "Long",
        "quantity": 0.001,
        "entry_price": 100.0,
        "stop_loss": 99.0,
        "take_profit": 101.0,
        "sl_order_id": 123,
        "tp_order_id": 456
    }
    mock_position_manager.get_position_by_symbol.return_value = position
    dynamic_trade_executor.strategy.analyze_and_adjust.return_value = {"command": "ADJUST_TP_SL", "stop_loss": 99.5, "take_profit": 101.5}
    mock_binance_client.create_stop_market_order.return_value = {"orderId": 789}
    mock_binance_client.create_take_profit_market_order.return_value = {"orderId": 987}

    await dynamic_trade_executor._handle_position_adjustment(position)

    dynamic_trade_executor.strategy.analyze_and_adjust.assert_called_once_with(position, dynamic_trade_executor.orderbook_manager)
    mock_binance_client.cancel_order.assert_any_call("BTCUSDT", 123)
    mock_binance_client.cancel_order.assert_any_call("BTCUSDT", 456)
    mock_binance_client.create_stop_market_order.assert_called_once_with("BTCUSDT", SIDE_SELL, 0.001, 99.5)
    mock_binance_client.create_take_profit_market_order.assert_called_once_with("BTCUSDT", SIDE_SELL, 0.001, 101.5)
    mock_position_manager.update_orders.assert_called_once_with("BTCUSDT", sl_order_id=789, tp_order_id=987)

@pytest.mark.asyncio
async def test_handle_position_adjustment_no_action(dynamic_trade_executor, mock_position_manager):
    position = {"symbol": "BTCUSDT", "side": "Long", "quantity": 0.001}
    mock_position_manager.get_position_by_symbol.return_value = position
    dynamic_trade_executor.strategy.analyze_and_adjust.return_value = None

    await dynamic_trade_executor._handle_position_adjustment(position)

    dynamic_trade_executor.strategy.analyze_and_adjust.assert_called_once_with(position, dynamic_trade_executor.orderbook_manager)
    mock_position_manager.update_orders.assert_not_called()
    mock_position_manager.close_position.assert_not_called()

# --- Тести для start_monitoring ---

@pytest.mark.asyncio
async def test_start_monitoring_with_open_position(default_trade_executor, mock_position_manager):
    mock_position_manager.get_position_by_symbol.return_value = {"symbol": "BTCUSDT", "side": "Long", "quantity": 0.001}
    default_trade_executor._handle_position_adjustment = AsyncMock()
    default_trade_executor._check_and_open_position = AsyncMock()

    # Імітуємо одне оновлення стакану
    await default_trade_executor.orderbook_manager.update_queue.put(True)

    # Запускаємо моніторинг на короткий час, щоб він обробив одне оновлення
    with patch.object(default_trade_executor.orderbook_manager.update_queue, 'get', side_effect=[True, asyncio.CancelledError]):
        try:
            await default_trade_executor.start_monitoring()
        except asyncio.CancelledError:
            pass

    default_trade_executor._handle_position_adjustment.assert_called_once_with(mock_position_manager.get_position_by_symbol.return_value)
    default_trade_executor._check_and_open_position.assert_not_called()

@pytest.mark.asyncio
async def test_start_monitoring_without_open_position(default_trade_executor, mock_position_manager):
    mock_position_manager.get_position_by_symbol.return_value = None
    default_trade_executor._handle_position_adjustment = AsyncMock()
    default_trade_executor._check_and_open_position = AsyncMock()

    # Імітуємо одне оновлення стакану
    await default_trade_executor.orderbook_manager.update_queue.put(True)

    # Запускаємо моніторинг на короткий час, щоб він обробив одне оновлення
    with patch.object(default_trade_executor.orderbook_manager.update_queue, 'get', side_effect=[True, asyncio.CancelledError]):
        try:
            await default_trade_executor.start_monitoring()
        except asyncio.CancelledError:
            pass

    default_trade_executor._handle_position_adjustment.assert_not_called()
    default_trade_executor._check_and_open_position.assert_called_once()

# --- Тести для _open_position ---

@pytest.mark.asyncio
async def test_open_position_market_order_long(default_trade_executor, mock_binance_client, mock_orchestrator, pending_symbols_set):
    default_trade_executor.strategy.params["entry_order_type"] = ORDER_TYPE_MARKET
    signal = {"signal_type": "Long", "wall_price": 100.0} # wall_price is not used for market order entry
    mock_binance_client.get_account_balance.return_value = 1000.0
    default_trade_executor.orderbook_manager.get_best_ask.return_value = 100.1

    await default_trade_executor._open_position(signal)

    mock_binance_client.futures_create_order.assert_called_once()
    args, kwargs = mock_binance_client.futures_create_order.call_args
    assert kwargs['symbol'] == "BTCUSDT"
    assert kwargs['side'] == SIDE_BUY
    assert kwargs['type'] == ORDER_TYPE_MARKET
    assert kwargs['quantity'] == pytest.approx(9.99) # (1000 * 0.1 * 10) / 100.1 = 9.99 -> floor to 0.999 with stepSize 0.001
    assert "newClientOrderId" in kwargs
    assert "BTCUSDT" in pending_symbols_set
    assert kwargs['newClientOrderId'] in mock_orchestrator.pending_sl_tp

@pytest.mark.asyncio
async def test_open_position_limit_order_short(default_trade_executor, mock_binance_client, mock_orchestrator, pending_symbols_set):
    default_trade_executor.strategy.params["entry_order_type"] = ORDER_TYPE_LIMIT
    default_trade_executor.strategy.params["entry_offset_ticks"] = 50
    signal = {"signal_type": "Short", "wall_price": 100.0}
    mock_binance_client.get_account_balance.return_value = 1000.0

    await default_trade_executor._open_position(signal)

    mock_binance_client.futures_create_order.assert_called_once()
    args, kwargs = mock_binance_client.futures_create_order.call_args
    assert kwargs['symbol'] == "BTCUSDT"
    assert kwargs['side'] == SIDE_SELL
    assert kwargs['type'] == ORDER_TYPE_LIMIT
    assert kwargs['price'] == "99.5" # 100.0 - (50 * 0.01)
    assert kwargs['quantity'] == pytest.approx(10.050) # (1000 * 0.1 * 10) / 99.5 = 10.05025 -> floor to 10.050
    assert kwargs['timeInForce'] == TIME_IN_FORCE_GTC
    assert "newClientOrderId" in kwargs
    assert "BTCUSDT" in pending_symbols_set
    assert kwargs['newClientOrderId'] in mock_orchestrator.pending_sl_tp

@pytest.mark.asyncio
async def test_open_position_quantity_zero(default_trade_executor, mock_binance_client, pending_symbols_set):
    default_trade_executor.strategy.params["entry_order_type"] = ORDER_TYPE_MARKET
    signal = {"signal_type": "Long", "wall_price": 100.0}
    mock_binance_client.get_account_balance.return_value = 0.0001 # Very small balance
    default_trade_executor.orderbook_manager.get_best_ask.return_value = 100.1

    await default_trade_executor._open_position(signal)

    mock_binance_client.futures_create_order.assert_not_called()
    assert "BTCUSDT" not in pending_symbols_set # Should be removed if quantity is zero

@pytest.mark.asyncio
async def test_open_position_error_handling(default_trade_executor, mock_binance_client, pending_symbols_set):
    default_trade_executor.strategy.params["entry_order_type"] = ORDER_TYPE_MARKET
    signal = {"signal_type": "Long", "wall_price": 100.0}
    mock_binance_client.get_account_balance.side_effect = Exception("API Error")

    await default_trade_executor._open_position(signal)

    mock_binance_client.futures_create_order.assert_not_called()
    assert "BTCUSDT" not in pending_symbols_set # Should be removed on error
