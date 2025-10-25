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
