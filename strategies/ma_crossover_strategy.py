import pandas as pd
from strategies.base_strategy import BaseStrategy
from loguru import logger

class MACrossoverStrategy(BaseStrategy):
    """
    Проста стратегія на основі перетину двох ковзних середніх (MA).
    """
    def __init__(self, strategy_id: str, symbol: str, params: dict):
        super().__init__(strategy_id, symbol)
        self.fast_ma_period = params.get('fast_ma', 10)
        self.slow_ma_period = params.get('slow_ma', 30)
        self.stop_loss_pct = params.get('stop_loss_pct', 0.02) # 2%
        self.take_profit_pct = params.get('take_profit_pct', 0.05) # 5%
        logger.info(f"[{self.strategy_id}] Ініціалізовано MACrossoverStrategy з параметрами: {params}")

    def check_signal(self, data: pd.DataFrame) -> dict | None:
        """
        Перевіряє сигнал на основі даних K-ліній.
        :param data: DataFrame з історичними даними, що містить колонку 'close'.
        """
        if len(data) < self.slow_ma_period:
            return None # Недостатньо даних для розрахунку MA

        # Розраховуємо ковзні середні
        data['fast_ma'] = data['close'].rolling(window=self.fast_ma_period).mean()
        data['slow_ma'] = data['close'].rolling(window=self.slow_ma_period).mean()

        # Остання свічка
        last = data.iloc[-1]
        # Попередня свічка
        prev = data.iloc[-2]

        # Перевірка на перетин
        # Long сигнал: швидка MA перетинає повільну знизу вгору
        if prev['fast_ma'] < prev['slow_ma'] and last['fast_ma'] > last['slow_ma']:
            logger.debug(f"[{self.strategy_id}] Сигнал LONG: fast_ma ({last['fast_ma']:.2f}) > slow_ma ({last['slow_ma']:.2f})")
            return {'signal_type': 'Long', 'price': last['close']}

        # Short сигнал: швидка MA перетинає повільну зверху вниз
        if prev['fast_ma'] > prev['slow_ma'] and last['fast_ma'] < last['slow_ma']:
            logger.debug(f"[{self.strategy_id}] Сигнал SHORT: fast_ma ({last['fast_ma']:.2f}) < slow_ma ({last['slow_ma']:.2f})")
            return {'signal_type': 'Short', 'price': last['close']}

        return None

    def calculate_sl_tp(self, entry_price: float, signal_type: str, **kwargs) -> dict:
        """
        Розраховує Stop-Loss та Take-Profit на основі відсотків.
        """
        if signal_type == 'Long':
            stop_loss = entry_price * (1 - self.stop_loss_pct)
            take_profit = entry_price * (1 + self.take_profit_pct)
        elif signal_type == 'Short':
            stop_loss = entry_price * (1 + self.stop_loss_pct)
            take_profit = entry_price * (1 - self.take_profit_pct)
        else:
            return {}

        return {'stop_loss': stop_loss, 'take_profit': take_profit}
