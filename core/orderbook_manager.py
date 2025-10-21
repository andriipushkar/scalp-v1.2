import asyncio
import pandas as pd
from loguru import logger

class OrderBookManager:
    """
    Керує локальною копією біржового стакану (Order Book) для одного символу.
    
    Цей клас відповідає за:
    1. Ініціалізацію стакану початковим \"знімком\" (snapshot) через REST API.
    2. Синхронізацію стакану в реальному часі за допомогою повідомлень з WebSocket-потоку.
    3. Надання доступу до даних про заявки на купівлю (bids) та продаж (asks).
    """
    def __init__(self, symbol: str):
        """
        Ініціалізує порожній стакан для вказаного символу.

        Args:
            symbol (str): Торговий символ (напр., 'BTCUSDT').
        """
        self.symbol = symbol
        # Створюємо порожні DataFrame з правильною структурою: індекс 'price' та колонка 'quantity'
        self._bids = pd.DataFrame([], columns=['price', 'quantity']).set_index('price')
        self._asks = pd.DataFrame([], columns=['price', 'quantity']).set_index('price')
        self.last_update_id = 0
        self._event_buffer = []  # Буфер для подій, що надходять під час ініціалізації
        self.is_initialized = False # Прапорець, що показує, чи стакан вже синхронізовано
        self.update_queue = asyncio.Queue() # Черга для сповіщення про оновлення стакану

    def _set_initial_snapshot(self, snapshot: dict):
        """Ініціалізує стакан початковим знімком, отриманим через REST API."""
        self.last_update_id = snapshot['lastUpdateId']
        
        # Ефективно створюємо DataFrame одразу з даних, конвертуючи типи
        self._bids = pd.DataFrame(snapshot['bids'], columns=['price', 'quantity'], dtype=float).set_index('price')
        self._asks = pd.DataFrame(snapshot['asks'], columns=['price', 'quantity'], dtype=float).set_index('price')
        
        logger.info(f"[{self.symbol}] Знімок стакану ініціалізовано. lastUpdateId: {self.last_update_id}")

    def _process_update(self, update: dict):
        """Оновлює стакан на основі даних з вебсокет-потоку @depth."""
        # b - заявки на купівлю (bids)
        for price_str, quantity_str in update['b']:
            price, quantity = float(price_str), float(quantity_str)
            if quantity == 0:
                # Якщо кількість 0, видаляємо рівень ціни зі стакану
                if price in self._bids.index:
                    self._bids = self._bids.drop(price)
            else:
                # Інакше оновлюємо або додаємо рівень
                self._bids.loc[price] = quantity
        
        # a - заявки на продаж (asks)
        for price_str, quantity_str in update['a']:
            price, quantity = float(price_str), float(quantity_str)
            if quantity == 0:
                if price in self._asks.index:
                    self._asks = self._asks.drop(price)
            else:
                self._asks.loc[price] = quantity

        # Сортуємо для підтримки коректного порядку: біди - по спаданню, аски - по зростанню
        self._bids.sort_index(ascending=False, inplace=True)
        self._asks.sort_index(ascending=True, inplace=True)

    async def initialize_book(self, snapshot: dict):
        """
        Фіналізує ініціалізацію стакану, обробляючи події з буферу,
        щоб синхронізувати стан з потоком вебсокету.
        """
        self._set_initial_snapshot(snapshot)
        
        # Обробляємо всі події, що накопичилися в буфері під час завантаження знімку
        logger.info(f"[{self.symbol}] Обробка {len(self._event_buffer)} буферизованих подій стакану...")
        for event in self._event_buffer:
            # Важливо: для ф'ючерсів Binance, перша подія після знімку може мати
            # `U` <= `lastUpdateId` та `u` >= `lastUpdateId` + 1.
            # Ми просто обробляємо всі накопичені події.
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

        # Перевірка послідовності оновлень (для ф'ючерсів може бути неактуальною)
        # `pu` - previous update id, `u` - final update id in this event
        # if msg['pu'] != self.last_update_id:
        #     logger.warning(f"[{self.symbol}] Пропущено оновлення стакану. Потрібна ресинхронізація.")
        #     # Тут можна додати логіку ресинхронізації
        #     return

        self._process_update(msg)
        self.last_update_id = msg['u']
        # Сповіщаємо TradeExecutor, що стакан оновився
        await self.update_queue.put(True)

    def get_bids(self) -> pd.DataFrame:
        """Повертає поточний стан заявок на купівлю (bids) у вигляді DataFrame."""
        return self._bids

    def get_asks(self) -> pd.DataFrame:
        """Повертає поточний стан заявок на продаж (asks) у вигляді DataFrame."""
        return self._asks

    def get_best_bid(self) -> float | None:
        """Повертає найкращу (найвищу) ціну купівлі."""
        if not self._bids.empty:
            return self._bids.index[0]
        return None

    def get_best_ask(self) -> float | None:
        """Повертає найкращу (найнижчу) ціну продажу."""
        if not self._asks.empty:
            return self._asks.index[0]
        return None
