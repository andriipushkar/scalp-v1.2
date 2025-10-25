# Довідник по API

Цей документ є технічним довідником для розробників, які бажають створювати власні торгові стратегії або модифікувати ядро бота QuantumTrader.

## 1. Створення власної стратегії

Основою для будь-якої нової торгової логіки є абстрактний клас `BaseStrategy`.

### `strategies/base_strategy.py`

Кожна нова стратегія повинна успадковувати клас `BaseStrategy` і реалізовувати його абстрактні методи.

**Приклад структури нової стратегії:**

```python
from strategies.base_strategy import BaseStrategy
from core.orderbook_manager import OrderBookManager
from core.binance_client import BinanceClient

class MyAwesomeStrategy(BaseStrategy):
    async def check_signal(self, order_book_manager: OrderBookManager, binance_client: BinanceClient) -> dict | None:
        # ... ваша логіка пошуку сигналу ...
        pass

    def calculate_sl_tp(self, entry_price: float, signal_type: str, order_book_manager: OrderBookManager, tick_size: float) -> dict | None:
        # ... ваша логіка розрахунку SL/TP ...
        pass
```

#### Абстрактні методи `BaseStrategy`

Ці методи **обов'язково** повинні бути реалізовані у вашому класі.

1.  `async def check_signal(self, order_book_manager, binance_client)`
    *   **Призначення:** Основний метод, який викликається для пошуку торгового сигналу.
    *   **Аргументи:**
        *   `order_book_manager` (`OrderBookManager`): Надає доступ до даних біржового стакану в реальному часі. Використовуйте `order_book_manager.get_bids()` та `get_asks()`.
        *   `binance_client` (`BinanceClient`): Надає доступ до методів API Binance, наприклад, для отримання історичних свічок (`get_klines`).
    *   **Повертає:** `dict` з інформацією про сигнал, якщо він знайдений (напр., `{'signal_type': 'Long', 'details': {...}}`), або `None`, якщо сигналу немає.

2.  `def calculate_sl_tp(self, entry_price, signal_type, order_book_manager, tick_size)`
    *   **Призначення:** Розраховує рівні Stop Loss та Take Profit для нового ордера.
    *   **Аргументи:**
        *   `entry_price` (`float`): Фактична ціна входу в позицію.
        *   `signal_type` (`str`): Тип сигналу (`'Long'` або `'Short'`).
        *   `order_book_manager` (`OrderBookManager`): Менеджер стакану для аналізу поточної ринкової ситуації.
        *   `tick_size` (`float`): Мінімальний крок ціни для поточного символу (для округлення).
    *   **Повертає:** `dict` з ключами `'stop_loss'` та `'take_profit'` або `None`.

#### Опціональні методи `BaseStrategy`

1.  `def analyze_and_adjust(self, position, order_book_manager)`
    *   **Призначення:** Дозволяє реалізувати логіку керування вже відкритою позицією (напр., трейлінг-стоп, передчасне закриття).
    *   **Аргументи:**
        *   `position` (`dict`): Словник з даними про відкриту позицію.
        *   `order_book_manager` (`OrderBookManager`): Менеджер стакану.
    *   **Повертає:** `dict` з командою на дію (напр., `{'command': 'CLOSE_POSITION'}`) або `None`.

---

## 2. Ключові компоненти ядра (Core)

Ось опис основних компонентів, з якими може взаємодіяти ваша стратегія.

### `core/orderbook_manager.py`

Керує локальною копією біржового стакану.

*   `get_bids() -> pd.DataFrame`
    *   Повертає DataFrame з поточними заявками на купівлю (bids). Індекс - ціна, колонка - кількість.
*   `get_asks() -> pd.DataFrame`
    *   Повертає DataFrame з поточними заявками на продаж (asks).
*   `get_best_bid() -> float | None`
    *   Повертає найкращу (найвищу) ціну купівлі.
*   `get_best_ask() -> float | None`
    *   Повертає найкращу (найнижчу) ціну продажу.

### `core/position_manager.py`

Керує станом усіх відкритих позицій.

*   `get_position_by_symbol(symbol: str) -> dict | None`
    *   Повертає інформацію про відкриту позицію для вказаного символу.
*   `get_all_positions() -> dict`
    *   Повертає словник з усіма активними позиціями.
*   `get_positions_count() -> int`
    *   Повертає кількість активних позицій.
