import pytest
import pytest_asyncio
import pandas as pd
from core.orderbook_manager import OrderBookManager

# Позначаємо всі тести в цьому файлі як асинхронні
pytestmark = pytest.mark.asyncio

@pytest.fixture
def sample_snapshot():
    """Фікстура, що надає приклад знімку стакану (snapshot) з REST API."""
    return {
        'lastUpdateId': 1000,
        'bids': [
            ['4100.00', '10'], # price, quantity
            ['4099.50', '5']
        ],
        'asks': [
            ['4100.50', '8'],
            ['4101.00', '12']
        ]
    }

@pytest_asyncio.fixture
async def initialized_obm(sample_snapshot):
    """Фікстура, що створює та ініціалізує OrderBookManager."""
    obm = OrderBookManager("TESTUSDT")
    await obm.initialize_book(sample_snapshot)
    return obm

async def test_initialization_from_snapshot(initialized_obm: OrderBookManager):
    """ТЕСТ: Перевіряє, чи стакан коректно ініціалізується знімком."""
    assert initialized_obm.is_initialized
    assert initialized_obm.last_update_id == 1000
    
    bids = initialized_obm.get_bids()
    asks = initialized_obm.get_asks()

    assert not bids.empty
    assert not asks.empty
    assert bids.index[0] == 4100.00 # Перевірка сортування (найкращий бід - найвищий)
    assert asks.index[0] == 4100.50 # Перевірка сортування (найкращий аск - найнижчий)
    assert bids.loc[4099.50]['quantity'] == 5
    assert asks.loc[4101.00]['quantity'] == 12

async def test_process_update_add_and_modify(initialized_obm: OrderBookManager):
    """ТЕСТ: Перевіряє додавання нового рівня та зміну існуючого."""
    update_msg = {
        'U': 1001, # firstUpdateId
        'u': 1002, # lastUpdateId
        'b': [
            ['4100.25', '20']  # Новий бід
        ],
        'a': [
            ['4101.00', '9']   # Зміна існуючого аску
        ]
    }

    await initialized_obm.process_depth_message(update_msg)
    
    bids = initialized_obm.get_bids()
    asks = initialized_obm.get_asks()

    assert 4100.25 in bids.index
    assert bids.loc[4100.25]['quantity'] == 20
    assert asks.loc[4101.00]['quantity'] == 9 # Перевіряємо, що кількість оновилася

async def test_process_update_remove_level(initialized_obm: OrderBookManager):
    """ТЕСТ: Перевіряє видалення рівня, коли його кількість стає 0."""
    update_msg = {
        'U': 1001,
        'u': 1002,
        'b': [
            ['4099.50', '0'] # Видалення існуючого біда
        ],
        'a': []
    }

    await initialized_obm.process_depth_message(update_msg)
    bids = initialized_obm.get_bids()
    assert 4099.50 not in bids.index # Перевіряємо, що рівень зник

async def test_event_buffering_before_initialization(sample_snapshot):
    """ТЕСТ: Перевіряє, що повідомлення буферизуються до повної ініціалізації."""
    obm = OrderBookManager("TESTUSDT")
    assert not obm.is_initialized
    assert len(obm._event_buffer) == 0

    # Імітуємо надходження повідомлення з WebSocket, коли стакан ще не ініціалізовано
    update_msg = {'U': 999, 'u': 999, 'b': [], 'a': []}
    await obm.process_depth_message(update_msg)

    assert not obm.is_initialized # Все ще не ініціалізовано
    assert len(obm._event_buffer) == 1 # Повідомлення потрапило в буфер

    # Тепер ініціалізуємо стакан
    await obm.initialize_book(sample_snapshot)

    assert obm.is_initialized # Тепер ініціалізовано
    assert len(obm._event_buffer) == 0 # Буфер має бути порожнім
