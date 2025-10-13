import asyncio
import pandas as pd
from loguru import logger

class OrderBookManager:
    """
    Керує локальною копією біржового стакану (Order Book) для одного символу.
    Відповідає за її синхронізацію в реальному часі.
    """
    def __init__(self, symbol: str):
        """Ініціалізує порожній стакан для вказаного символу."""
        self.symbol = symbol
        # Використовуємо pandas DataFrame для ефективної роботи з даними стакану
        self._bids = pd.DataFrame(columns=['price', 'quantity'])
        self._asks = pd.DataFrame(columns=['price', 'quantity'])
        self.last_update_id = 0
        self._event_buffer = []  # Буфер для подій, що надходять під час ініціалізації
        self.is_initialized = False
        self.update_queue = asyncio.Queue()

    def _set_initial_snapshot(self, snapshot: dict):
        """Ініціалізує стакан початковим знімком, отриманим через REST API."""
        self.last_update_id = snapshot['lastUpdateId']
        
        bids_data = [{'price': float(p), 'quantity': float(q)} for p, q in snapshot['bids']]
        self._bids = pd.DataFrame(bids_data).set_index('price')
        
        asks_data = [{'price': float(p), 'quantity': float(q)} for p, q in snapshot['asks']]
        self._asks = pd.DataFrame(asks_data).set_index('price')
        
        logger.info(f"[{self.symbol}] Знімок стакану ініціалізовано. lastUpdateId: {self.last_update_id}")

    def _process_update(self, update: dict):
        """Оновлює стакан на основі даних з вебсокет-потоку @depth."""
        # b - заявки на купівлю (bids)
        for bid in update['b']:
            price, quantity = float(bid[0]), float(bid[1])
            if quantity == 0:
                # Якщо кількість 0, видаляємо рівень ціни
                if price in self._bids.index:
                    self._bids = self._bids.drop(price)
            else:
                # Інакше оновлюємо або додаємо рівень
                self._bids.loc[price] = quantity
        
        # a - заявки на продаж (asks)
        for ask in update['a']:
            price, quantity = float(ask[0]), float(ask[1])
            if quantity == 0:
                if price in self._asks.index:
                    self._asks = self._asks.drop(price)
            else:
                self._asks.loc[price] = quantity

        # Сортуємо для коректного відображення (біди - по спаданню, аски - по зростанню)
        self._bids.sort_index(ascending=False, inplace=True)
        self._asks.sort_index(ascending=True, inplace=True)

    async def initialize_book(self, snapshot: dict):
        """
        Фіналізує ініціалізацію стакану, обробляючи події з буферу,
        щоб синхронізувати стан з потоком вебсокету.
        """
        self._set_initial_snapshot(snapshot)
        
        # Відкидаємо застарілі події з буферу
        # Примітка: для ф'ючерсів lastUpdateId зі знімку не синхронізований з U/u з вебсокету.
        # Тому ми просто обробляємо всі події, що накопичилися.
        logger.info(f"[{self.symbol}] Обробка {len(self._event_buffer)} буферизованих подій стакану...")
        for event in self._event_buffer:
            self._process_update(event)
            self.last_update_id = event['u']
        
        self._event_buffer = []  # Очищуємо буфер
        self.is_initialized = True
        logger.success(f"[{self.symbol}] Біржовий стакан успішно ініціалізовано та синхронізовано.")

    async def process_depth_message(self, msg: dict):
        """
        Обробляє нове повідомлення з вебсокет-потоку @depth.
        """
        # Якщо стакан ще не ініціалізовано, складаємо події в буфер
        if not self.is_initialized:
            self._event_buffer.append(msg)
            return

        # Оскільки ID знімку та вебсокету для ф'ючерсів не збігаються, 
        # ми відмовилися від суворої перевірки і просто застосовуємо оновлення.
        self._process_update(msg)
        self.last_update_id = msg['u']
        await self.update_queue.put(True)

    def get_bids(self):
        """Повертає поточний стан заявок на купівлю (bids)."""
        return self._bids

    def get_asks(self):
        """Повертає поточний стан заявок на продаж (asks)."""
        return self._asks