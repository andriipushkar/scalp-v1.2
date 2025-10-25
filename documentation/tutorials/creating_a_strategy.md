# Туторіал: Створення власної стратегії

Цей посібник крок за кроком покаже, як створити, інтегрувати та запустити власну торгову стратегію в боті QuantumTrader. Як приклад ми створимо просту стратегію на основі перетину двох ковзних середніх (MA Crossover).

## Крок 1: Створення файлу стратегії

1.  Перейдіть до папки `strategies/`.
2.  Створіть новий Python файл. Назвемо його `simple_ma_crossover_strategy.py`.

## Крок 2: Написання коду стратегії

Вставте наступний код у ваш новий файл. Нижче ми розберемо кожну частину.

```python
# strategies/simple_ma_crossover_strategy.py

from strategies.base_strategy import BaseStrategy
import pandas as pd

class SimpleMACrossoverStrategy(BaseStrategy):
    """
    Дуже проста і демонстраційна стратегія, заснована на перетині двох ковзних середніх (MA).
    Призначена для навчальних цілей.
    """

    def __init__(self, strategy_id: str, symbol: str, settings: dict):
        """
        Ініціалізація стратегії.
        """
        super().__init__(strategy_id, symbol, settings)
        # Ці параметри будуть завантажені з файлу конфігурації
        self.fast_ma_period = self.settings.get('fast_ma_period', 7)
        self.slow_ma_period = self.settings.get('slow_ma_period', 25)

    async def check_signal(self):
        """
        Основний метод, який перевіряє наявність торгового сигналу.
        """
        # Отримуємо дані K-ліній (свічок) з кешу
        klines = self.get_klines()
        if klines is None or len(klines) < self.slow_ma_period:
            # Якщо даних недостатньо, нічого не робимо
            return None

        # Створюємо DataFrame з pandas для легкого розрахунку індикаторів
        df = pd.DataFrame(klines, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['close'] = pd.to_numeric(df['close'])

        # Розраховуємо швидку та повільну ковзні середні
        df['fast_ma'] = df['close'].rolling(window=self.fast_ma_period).mean()
        df['slow_ma'] = df['close'].rolling(window=self.slow_ma_period).mean()

        # --- Логіка сигналу ---

        # Беремо дві останні свічки для аналізу
        last_row = df.iloc[-1]
        prev_row = df.iloc[-2]

        # Сигнал на покупку (Long)
        # Швидка MA перетнула повільну MA знизу вгору
        if prev_row['fast_ma'] < prev_row['slow_ma'] and last_row['fast_ma'] > last_row['slow_ma']:
            self.logger.info(f"[{self.strategy_id}] Сигнал на ПОКУПКУ для {self.symbol}")
            return 1  # 1 означає сигнал на покупку

        # Сигнал на продаж (Short)
        # Швидка MA перетнула повільну MA зверху вниз
        if prev_row['fast_ma'] > prev_row['slow_ma'] and last_row['fast_ma'] < last_row['slow_ma']:
            self.logger.info(f"[{self.strategy_id}] Сигнал на ПРОДАЖ для {self.symbol}")
            return -1  # -1 означає сигнал на продаж

        return None  # Немає сигналу

    def calculate_sl_tp(self, entry_price: float, signal: int):
        """
        Розрахунок рівнів Stop Loss та Take Profit.
        Для цієї простої стратегії ми будемо використовувати фіксований відсоток.
        """
        sl_pct = self.settings.get('sl_pct', 0.01)  # 1% Stop Loss
        tp_pct = self.settings.get('tp_pct', 0.02)  # 2% Take Profit

        if signal == 1:  # Для Long позиції
            stop_loss = entry_price * (1 - sl_pct)
            take_profit = entry_price * (1 + tp_pct)
        elif signal == -1:  # Для Short позиції
            stop_loss = entry_price * (1 + sl_pct)
            take_profit = entry_price * (1 - sl_pct)
        else:
            return None, None

        return stop_loss, take_profit
```

### Розбір коду

1.  **Клас та успадкування:**
    *   Ваша стратегія повинна успадковувати клас `BaseStrategy` з файлу `strategies/base_strategy.py`.
    *   Назва класу повинна бути унікальною (напр., `SimpleMACrossoverStrategy`).

2.  **`__init__(self, ...)`:**
    *   Це конструктор класу. Він викликає конструктор базового класу `super().__init__(...)`.
    *   Тут ви завантажуєте параметри вашої стратегії з об'єкту `settings`. Рекомендується використовувати метод `.get()` для встановлення значень за замовчуванням.

3.  **`async def check_signal(self)`:**
    *   Це "серце" вашої стратегії. Бот викликає цей метод на кожному новому тіку ринкових даних.
    *   Ваше завдання — проаналізувати дані та повернути `1` (сигнал на покупку), `-1` (сигнал на продаж) або `None` (немає сигналу).
    *   Ви можете отримати K-лінії за допомогою `self.get_klines()`.

4.  **`def calculate_sl_tp(self, ...)`:**
    *   Цей метод викликається, коли `check_signal` повертає сигнал. Він повинен розрахувати та повернути ціни для Stop Loss та Take Profit.
    *   В нашому прикладі ми використовуємо простий фіксований відсоток, але ви можете використовувати більш складну логіку (наприклад, на основі ATR або локальних екстремумів).

## Крок 3: Створення файлу конфігурації

1.  Перейдіть до папки `configs/strategies/`.
2.  Створіть новий YAML файл. Назвемо його `simple_ma_crossover.yaml`.
3.  Додайте в нього параметри для вашої стратегії:

    ```yaml
    default:
      fast_ma_period: 7
      slow_ma_period: 25
      sl_pct: 0.01  # 1% Stop Loss
      tp_pct: 0.02  # 2% Take Profit
    ```

## Крок 4: Інтеграція в головний конфіг

Тепер потрібно сказати боту, щоб він використовував вашу нову стратегію.

1.  **Імпортуйте вашу стратегію:**
    *   Відкрийте файл `main.py`.
    *   Знайдіть секцію, де імпортуються інші стратегії, і додайте імпорт вашої:
        ```python
        from strategies.simple_ma_crossover_strategy import SimpleMACrossoverStrategy
        ```

2.  **Додайте стратегію до `STRATEGY_MAPPING`:**
    *   У файлі `main.py` знайдіть словник `STRATEGY_MAPPING`.
    *   Додайте в нього вашу стратегію, щоб бот знав, як знайти клас за назвою:
        ```python
        STRATEGY_MAPPING = {
            # ... інші стратегії
            "SimpleMACrossoverStrategy": SimpleMACrossoverStrategy,
        }
        ```

3.  **Активуйте стратегію в `config.yaml`:**
    *   Відкрийте файл `configs/config.yaml`.
    *   Додайте `SimpleMACrossoverStrategy` до списку `enabled_strategies`.
    *   Вкажіть шлях до файлу з налаштуваннями в `strategy_settings`.

    ```yaml
    enabled_strategies:
      - EmaTrendFollowingStrategy
      - SimpleMACrossoverStrategy  # <--- Додайте сюди

    strategy_settings:
      EmaTrendFollowingStrategy: "configs/strategies/ema_trend_following.yaml"
      SimpleMACrossoverStrategy: "configs/strategies/simple_ma_crossover.yaml" # <--- І сюди
    ```

## Крок 5: Запуск та тестування

Все готово! Тепер ви можете запустити бота, і він буде використовувати вашу нову стратегію.

```bash
python main.py
```

Слідкуйте за логами, щоб побачити повідомлення від вашої стратегії.

Вітаємо, ви створили та інтегрували свою першу торгову стратегію! Тепер ви можете експериментувати, додаючи більш складну логіку, нові індикатори та методи управління ризиками.

---

**Примітка для майбутніх покращень:**

На даний момент реєстрація нової стратегії вимагає редагування файлу `main.py`. В ідеалі, система повинна автоматично знаходити та реєструвати нові стратегії з папки `strategies/`. Це може бути одним з напрямків для майбутнього покращення проекту, що зробить додавання нових стратегій ще простішим.
