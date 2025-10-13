import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from core.bot_orchestrator import BotOrchestrator
from binance.enums import *

# Позначаємо всі тести в цьому файлі як асинхронні
pytestmark = pytest.mark.asyncio

@pytest.fixture
def orchestrator() -> BotOrchestrator:
    """Фікстура для створення екземпляру BotOrchestrator для кожного тесту."""
    # Використовуємо patch, щоб "заглушити" завантаження конфігів з файлів
    with patch('core.bot_orchestrator.BotOrchestrator._load_json') as mock_load_json:
        # Імітуємо, що конфіги порожні або мають базові значення
        mock_load_json.return_value = []
        orchestrator_instance = BotOrchestrator()
        # Створюємо мок (заглушку) для BinanceClient
        orchestrator_instance.binance_client = AsyncMock()
        # Створюємо мок для PositionManager
        orchestrator_instance.position_manager = MagicMock()
        return orchestrator_instance

async def test_handle_filled_entry_order_places_sl_and_tp(orchestrator: BotOrchestrator):
    """
    ТЕСТ: Перевіряє, чи при отриманні повідомлення про виконання ордеру на вхід,
    оркестратор коректно виставляє Stop-Loss та Take-Profit ордери.
    """
    # --- 1. Підготовка (Arrange) ---
    symbol = "BTCUSDT"
    client_order_id = "test_client_id_123"
    quantity = 0.01
    sl_price = 60000.0
    tp_price = 62000.0
    strategy_id = "test_strategy"
    tick_size = 0.01

    # Імітуємо, що ми відправили ордер на вхід і чекаємо його виконання
    orchestrator.pending_sl_tp[client_order_id] = {
        'signal_type': "Long",
        'strategy_id': strategy_id,
        'quantity': quantity
    }

    # Створюємо мок-об'єкт для стратегії, який поверне розраховані SL/TP
    mock_strategy = MagicMock()
    mock_strategy.calculate_sl_tp.return_value = {
        'stop_loss': sl_price,
        'take_profit': tp_price
    }
    
    # Створюємо мок-об'єкт для виконавця (executor) з усіма потрібними атрибутами
    mock_executor = MagicMock()
    mock_executor.strategy = mock_strategy
    mock_executor.strategy_id = strategy_id
    mock_executor.price_precision = 2
    mock_executor.orderbook_manager = MagicMock()
    mock_executor.tick_size = tick_size
    orchestrator.trade_executors.append(mock_executor)

    # Створюємо фейкове повідомлення від WebSocket
    fake_ws_message = {
        'e': 'ORDER_TRADE_UPDATE',
        'o': {
            's': symbol,
            'c': client_order_id,
            'i': 123456789,
            'X': 'FILLED',
            'ot': 'LIMIT',
            'ap': '61000.0',
            'q': str(quantity)
        }
    }

    # --- 2. Дія (Act) ---
    await orchestrator._handle_user_data_message(fake_ws_message)

    # --- 3. Перевірка (Assert) ---
    # Перевіряємо, що був викликаний наш новий метод розрахунку SL/TP з правильними аргументами
    mock_strategy.calculate_sl_tp.assert_called_once_with(
        entry_price=61000.0, 
        signal_type="Long",
        order_book_manager=mock_executor.orderbook_manager,
        tick_size=mock_executor.tick_size
    )

    # Перевіряємо, що були викликані методи для створення ордерів
    orchestrator.binance_client.create_stop_market_order.assert_called_once()
    orchestrator.binance_client.create_take_profit_market_order.assert_called_once()

    # Перевіряємо, що позиція була збережена в PositionManager
    orchestrator.position_manager.set_position.assert_called_once()

async def test_handle_filled_exit_order(orchestrator: BotOrchestrator):
    """
    ТЕСТ: Перевіряє коректне закриття позиції при спрацюванні SL або TP.
    """
    # --- 1. Підготовка (Arrange) ---
    symbol = "BTCUSDT"
    sl_order_id = 12345
    tp_order_id = 67890

    # Імітуємо, що у нас є відкрита позиція
    orchestrator.position_manager.get_position_by_symbol.return_value = {
        'sl_order_id': sl_order_id,
        'tp_order_id': tp_order_id
    }

    # Імітуємо повідомлення про спрацювання Stop-Loss ордеру
    fake_ws_message = {
        'e': 'ORDER_TRADE_UPDATE',
        'o': {
            's': symbol,
            'i': sl_order_id, # ID виконаного ордеру
            'X': 'FILLED',
            'ot': 'STOP_MARKET',
            'c': 'dummy_client_id' # Client Order ID не важливий для цієї логіки
        }
    }

    # --- 2. Дія (Act) ---
    await orchestrator._handle_user_data_message(fake_ws_message)

    # --- 3. Перевірка (Assert) ---
    # Перевіряємо, що бот спробував скасувати парний ордер (Take-Profit)
    orchestrator.binance_client.cancel_order.assert_called_once_with(symbol, tp_order_id)
    # Перевіряємо, що бот закрив позицію у своєму менеджері
    orchestrator.position_manager.close_position.assert_called_once_with(symbol)

async def test_handle_canceled_entry_order(orchestrator: BotOrchestrator):
    """
    ТЕСТ: Перевіряє, що бот очищує "очікуючий" ордер, якщо його було скасовано.
    """
    # --- 1. Підготовка (Arrange) ---
    client_order_id = "test_client_id_456"
    symbol = "ETHUSDT"

    # Імітуємо, що ми чекаємо на виконання цього ордеру
    orchestrator.pending_sl_tp[client_order_id] = {'signal_type': 'Long'}
    orchestrator.pending_symbols.add(symbol)

    assert client_order_id in orchestrator.pending_sl_tp
    assert symbol in orchestrator.pending_symbols

    # Імітуємо повідомлення про скасування ордеру
    fake_ws_message = {
        'e': 'ORDER_TRADE_UPDATE',
        'o': {
            's': symbol,
            'c': client_order_id,
            'X': 'CANCELED',
            'ot': 'LIMIT',
            'i': 98765
        }
    }

    # --- 2. Дія (Act) ---
    await orchestrator._handle_user_data_message(fake_ws_message)

    # --- 3. Перевірка (Assert) ---
    # Перевіряємо, що ордер було видалено зі списків очікування
    assert client_order_id not in orchestrator.pending_sl_tp
    assert symbol not in orchestrator.pending_symbols
