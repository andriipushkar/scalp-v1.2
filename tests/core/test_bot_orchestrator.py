import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, mock_open
import yaml

from core.bot_orchestrator import BotOrchestrator
from core.position_manager import PositionManager # Import for spec
from binance.enums import *

# Позначаємо всі тести в цьому файлі як асинхронні
pytestmark = pytest.mark.asyncio

@pytest.fixture
def mock_config():
    """Фікстура, що надає тестову конфігурацію."""
    return {
        'symbols': ['BTCUSDT', 'ETHUSDT'],
        'enabled_strategies': ['TestStrategy'],
        'strategy_settings': {
            'TestStrategy': 'configs/strategies/test_strategy.yaml'
        },
        'trading_parameters': {
            'leverage': 20,
            'margin_type': 'ISOLATED',
            'max_concurrent_symbols': 5,
            'max_active_trades': 10 # Додано відсутній ключ
        }
    }

@pytest.fixture
def mock_strategy_params():
    """Фікстура для параметрів тестової стратегії."""
    return {
        'default': {'param1': 10},
        'symbol_specific': {
            'BTCUSDT': {'param1': 15}
        }
    }

@pytest.fixture
def orchestrator(mock_config, mock_strategy_params):
    """
    Фікстура для створення екземпляру BotOrchestrator з моками конфігураційних файлів.
    """
    m_open = mock_open()
    
    def read_side_effect(path, *args, **kwargs):
        if 'config.yaml' in path:
            return mock_open(read_data=yaml.dump(mock_config))()
        elif 'test_strategy.yaml' in path:
            return mock_open(read_data=yaml.dump(mock_strategy_params))()
        else:
            return mock_open(read_data="")()

    with patch('builtins.open', side_effect=read_side_effect):
        with patch('core.bot_orchestrator.import_module') as mock_import:
            MockStrategyClass = MagicMock()
            mock_strategy_instance = MagicMock()
            mock_strategy_instance.strategy_id = "TestStrategy_BTCUSDT"
            mock_strategy_instance.symbol = "BTCUSDT"
            MockStrategyClass.return_value = mock_strategy_instance
            
            mock_module = MagicMock()
            # Налаштовуємо getattr, щоб він повертав наш мок класу
            def getattr_side_effect(module, class_name):
                if class_name == 'TestStrategy':
                    return MockStrategyClass
                return MagicMock()
            mock_module.TestStrategy = MockStrategyClass
            mock_import.return_value = mock_module

            orchestrator_instance = BotOrchestrator(config_path='configs/config.yaml')
            
            orchestrator_instance.binance_client = AsyncMock()
            
            # Використовуємо MagicMock для PositionManager, бо більшість його методів синхронні
            pm_mock = MagicMock(spec=PositionManager)
            pm_mock.reconcile_with_exchange = AsyncMock() # Мокаємо асинхронний метод окремо
            orchestrator_instance.position_manager = pm_mock
            
            orchestrator_instance.MockStrategyClass = MockStrategyClass
            
            yield orchestrator_instance


@patch('core.bot_orchestrator.BinanceClient')
async def test_start_initializes_correct_executors(MockBinanceClient, orchestrator: BotOrchestrator, mock_config, mock_strategy_params):
    """
    ТЕСТ: Перевіряє, чи метод start правильно ініціалізує TradeExecutor-ів
    на основі конфігурації.
    """
    # --- 1. Підготовка (Arrange) ---
    # Налаштовуємо мок, який буде використано в `async with`
    mock_client = AsyncMock()
    
    # get_async_client - це синхронний метод, тому його треба мокати окремо
    underlying_client_mock = MagicMock()
    underlying_client_mock.tld = 'com' # BinanceSocketManager очікує цей атрибут
    mock_client.get_async_client = MagicMock(return_value=underlying_client_mock)

    mock_client.get_symbol_info.return_value = {
        'pricePrecision': 2, 'quantityPrecision': 3, 'filters': [{'tickSize': '0.01'}]
    }
    mock_client.get_futures_order_book.return_value = {
        'bids': [['60000.0', '10.0']], 
        'asks': [['60001.0', '12.0']], 
        'lastUpdateId': 12345
    }
    MockBinanceClient.return_value.__aenter__.return_value = mock_client

    # Патчимо асинхронні задачі та їх збір, щоб вони не запускались реально
    with patch('asyncio.create_task'), patch('asyncio.gather', new_callable=AsyncMock):
        # --- 2. Дія (Act) ---
        await orchestrator.start()

        # --- 3. Перевірка (Assert) ---
        assert len(orchestrator.trade_executors) == 2
        
        final_params_btc = mock_strategy_params['default'].copy()
        final_params_btc.update(mock_strategy_params['symbol_specific']['BTCUSDT'])
        
        orchestrator.MockStrategyClass.assert_any_call(
            "TestStrategy_BTCUSDT", "BTCUSDT", final_params_btc
        )
        
        final_params_eth = mock_strategy_params['default'].copy()
        orchestrator.MockStrategyClass.assert_any_call(
            "TestStrategy_ETHUSDT", "ETHUSDT", final_params_eth
        )
        
        # Перевірки тепер мають бути на екземплярі мок-клієнта
        assert mock_client.set_leverage.call_count == 2
        assert mock_client.set_margin_type.call_count == 2


async def test_handle_filled_entry_order_places_sl_and_tp(orchestrator: BotOrchestrator):
    """
    ТЕСТ: Перевіряє, чи при отриманні повідомлення про виконання ордеру на вхід,
    оркестратор коректно виставляє Stop-Loss та Take-Profit ордери.
    """
    symbol = "BTCUSDT"
    client_order_id = "test_client_id_123"
    quantity = 0.01
    sl_price = 60000.0
    tp_price = 62000.0
    strategy_id = "TestStrategy_BTCUSDT"
    tick_size = 0.01

    orchestrator.pending_sl_tp[client_order_id] = {
        'signal_type': "Long",
        'strategy_id': strategy_id,
        'quantity': quantity
    }

    mock_strategy = MagicMock()
    mock_strategy.calculate_sl_tp.return_value = {'stop_loss': sl_price, 'take_profit': tp_price}
    
    mock_executor = MagicMock()
    mock_executor.strategy = mock_strategy
    mock_executor.strategy_id = strategy_id
    mock_executor.price_precision = 2
    mock_executor.qty_precision = 3
    mock_executor.orderbook_manager = MagicMock()
    mock_executor.tick_size = tick_size
    orchestrator.trade_executors.append(mock_executor)

    fake_ws_message = {
        'e': 'ORDER_TRADE_UPDATE',
        'o': {
            's': symbol, 'c': client_order_id, 'i': 12345, 'X': 'FILLED',
            'ot': 'LIMIT', 'ap': '61000.0', 'q': str(quantity)
        }
    }

    # --- 2. Дія (Act) ---
    await orchestrator._handle_user_data_message(fake_ws_message)

    # --- 3. Перевірка (Assert) ---
    mock_strategy.calculate_sl_tp.assert_called_once_with(
        entry_price=61000.0, signal_type="Long",
        order_book_manager=mock_executor.orderbook_manager,
        tick_size=mock_executor.tick_size
    )
    orchestrator.binance_client.create_stop_market_order.assert_called_once()
    orchestrator.binance_client.create_take_profit_market_order.assert_called_once()
    orchestrator.position_manager.set_position.assert_called_once()

async def test_handle_filled_exit_order(orchestrator: BotOrchestrator):
    """
    ТЕСТ: Перевіряє коректне закриття позиції при спрацюванні SL або TP.
    """
    symbol = "BTCUSDT"
    sl_order_id = 12345
    tp_order_id = 67890

    # Тепер get_position_by_symbol є синхронним методом на MagicMock
    orchestrator.position_manager.get_position_by_symbol.return_value = {
        'sl_order_id': sl_order_id, 'tp_order_id': tp_order_id
    }

    fake_ws_message = {
        'e': 'ORDER_TRADE_UPDATE',
        'o': {'s': symbol, 'i': sl_order_id, 'X': 'FILLED', 'ot': 'STOP_MARKET', 'c': 'dummy'}
    }

    # --- 2. Дія (Act) ---
    await orchestrator._handle_user_data_message(fake_ws_message)

    # --- 3. Перевірка (Assert) ---
    orchestrator.binance_client.cancel_order.assert_called_once_with(symbol, tp_order_id)
    orchestrator.position_manager.close_position.assert_called_once_with(symbol)

async def test_handle_canceled_entry_order(orchestrator: BotOrchestrator):
    """
    ТЕСТ: Перевіряє, що бот очищує "очікуючий" ордер, якщо його було скасовано.
    """
    client_order_id = "test_client_id_456"
    symbol = "ETHUSDT"

    orchestrator.pending_sl_tp[client_order_id] = {'signal_type': 'Long'}
    orchestrator.pending_symbols.add(symbol)

    fake_ws_message = {
        'e': 'ORDER_TRADE_UPDATE',
        'o': {'s': symbol, 'c': client_order_id, 'X': 'CANCELED', 'ot': 'LIMIT', 'i': 98765}
    }

    # --- 2. Дія (Act) ---
    await orchestrator._handle_user_data_message(fake_ws_message)

    # --- 3. Перевірка (Assert) ---
    assert client_order_id not in orchestrator.pending_sl_tp
    assert symbol not in orchestrator.pending_symbols