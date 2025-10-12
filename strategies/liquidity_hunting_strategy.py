from strategies.base_strategy import BaseStrategy
from core.orderbook_manager import OrderBookManager
import pandas as pd
from loguru import logger

class LiquidityHuntingStrategy(BaseStrategy):
    """
    Стратегія, що шукає великі "стіни" ліквідності в біржовому стакані
    і намагається виставити ордер перед ними (front-running).
    """
    def __init__(self, strategy_id: str, symbol: str, params: dict):
        """Ініціалізує стратегію з її унікальним ID, символом та параметрами."""
        super().__init__(strategy_id, symbol)
        self.params = params
        logger.info(f"[{self.strategy_id}] Ініціалізовано LiquidityHuntingStrategy з параметрами: {self.params}")

    def check_signal(self, order_book_manager: OrderBookManager):
        """
        Перевіряє наявність торгового сигналу на основі поточного стану біржового стакану.
        Ця стратегія, на відміну від попередньої, використовує стакан, а не свічки.
        """
        bids = order_book_manager.get_bids()
        asks = order_book_manager.get_asks()

        if bids.empty or asks.empty:
            return None

        # --- Завантаження параметрів з конфігурації ---
        wall_multiplier = self.params.get('wall_volume_multiplier', 10)
        activation_ticks = self.params.get('activation_distance_ticks', 15)
        
        # Отримуємо найкращі ціни для визначення спреду та поточної ринкової ситуації
        best_bid_price = bids.index[0]
        best_ask_price = asks.index[0]

        # --- Логіка для LONG сигналу (полювання на "стіну" на купівлю) ---
        # Розраховуємо середній об'єм на перших 20 рівнях стакану
        avg_bid_volume = bids['quantity'].head(20).mean()
        # Шукаємо рівні, де об'єм значно перевищує середній
        bid_walls = bids[bids['quantity'] > avg_bid_volume * wall_multiplier]
        
        if not bid_walls.empty:
            wall_price = bid_walls.index[0]
            wall_volume = bid_walls.iloc[0]['quantity']
            
            # Перевіряємо, чи ціна знаходиться на достатній відстані для активації
            price_distance_to_wall = best_ask_price - wall_price
            tick_size = self._get_tick_size(bids.index.to_series())
            distance_in_ticks = price_distance_to_wall / tick_size if tick_size > 0 else float('inf')

            if 0 < distance_in_ticks <= activation_ticks:
                logger.info(f"[{self.strategy_id}] Потенційний LONG сигнал. Знайдено 'стіну' на купівлю: {wall_volume:.2f} @ {wall_price}. Поточна ціна за {distance_in_ticks:.1f} тіків.")
                # TODO: Додати валідацію сигналу за допомогою стрічки угод (Time & Sales) для фільтрації спуфінгу.
                return {
                    'signal_type': 'Long',
                    'entry_price': wall_price, # Спрощено: ціна входу - це ціна стіни. Реальна ціна буде розрахована в calculate_sl_tp
                    'wall_price': wall_price
                }

        # --- Логіка для SHORT сигналу (полювання на "стіну" на продаж) ---
        avg_ask_volume = asks['quantity'].head(20).mean()
        ask_walls = asks[asks['quantity'] > avg_ask_volume * wall_multiplier]

        if not ask_walls.empty:
            wall_price = ask_walls.index[0]
            wall_volume = ask_walls.iloc[0]['quantity']

            price_distance_to_wall = wall_price - best_bid_price
            tick_size = self._get_tick_size(asks.index.to_series())
            distance_in_ticks = price_distance_to_wall / tick_size if tick_size > 0 else float('inf')

            if 0 < distance_in_ticks <= activation_ticks:
                logger.info(f"[{self.strategy_id}] Потенційний SHORT сигнал. Знайдено 'стіну' на продаж: {wall_volume:.2f} @ {wall_price}. Поточна ціна за {distance_in_ticks:.1f} тіків.")
                # TODO: Додати валідацію сигналу за допомогою стрічки угод (Time & Sales) для фільтрації спуфінгу.
                return {
                    'signal_type': 'Short',
                    'entry_price': wall_price,
                    'wall_price': wall_price
                }

        return None

    def calculate_sl_tp(self, entry_price: float, signal_type: str, **kwargs):
        """
        Розраховує ціну входу, Stop-Loss та Take-Profit на основі ціни "стіни".
        """
        wall_price = kwargs.get('wall_price')
        if not wall_price:
            raise ValueError("Для цієї стратегії необхідно передати 'wall_price'")

        # Завантаження параметрів зміщення
        entry_offset_ticks = self.params.get('entry_offset_ticks', 2)
        sl_offset_ticks = self.params.get('stop_loss_offset_ticks', 2)
        rrr = self.params.get('risk_reward_ratio', 1.5)
        
        tick_size = kwargs.get('tick_size')
        if not tick_size:
            raise ValueError("Для розрахунку SL/TP необхідно передати 'tick_size'")

        if signal_type == 'Long':
            # Вхід трохи вище за стіну
            entry_price = wall_price + (entry_offset_ticks * tick_size)
            # Стоп-лос трохи нижче за стіну
            stop_loss = wall_price - (sl_offset_ticks * tick_size)
            sl_distance = entry_price - stop_loss
            take_profit = entry_price + (sl_distance * rrr)
        elif signal_type == 'Short':
            # Вхід трохи нижче за стіну
            entry_price = wall_price - (entry_offset_ticks * tick_size)
            # Стоп-лос трохи вище за стіну
            stop_loss = wall_price + (sl_offset_ticks * tick_size)
            sl_distance = stop_loss - entry_price
            take_profit = entry_price - (sl_distance * rrr)
        else:
            return None

        return {
            'entry_price': entry_price,
            'stop_loss': stop_loss,
            'take_profit': take_profit
        }

    def _get_tick_size(self, price_series: pd.Series) -> float:
        """Розраховує мінімальний крок ціни (тік) на основі даних стакану."""
        if len(price_series) < 2:
            return 0.01 # Значення за замовчуванням, якщо не вдалося розрахувати
        # Знаходимо мінімальну різницю між сусідніми рівнями цін
        tick = price_series.diff().abs().min()
        return tick if pd.notna(tick) and tick > 0 else 0.01