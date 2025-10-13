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

    def _find_opposite_wall(self, order_book_manager: OrderBookManager, signal_type: str) -> float | None:
        """Знаходить ціну найближчої стіни ліквідності у протилежному напрямку."""
        wall_multiplier = self.params.get('wall_volume_multiplier', 10)

        if signal_type == 'Long':
            asks = order_book_manager.get_asks()
            if asks.empty: return None
            avg_ask_volume = asks['quantity'].head(20).mean()
            ask_walls = asks[asks['quantity'] > avg_ask_volume * wall_multiplier]
            return ask_walls.index[0] if not ask_walls.empty else None
        
        elif signal_type == 'Short':
            bids = order_book_manager.get_bids()
            if bids.empty: return None
            avg_bid_volume = bids['quantity'].head(20).mean()
            bid_walls = bids[bids['quantity'] > avg_bid_volume * wall_multiplier]
            return bid_walls.index[0] if not bid_walls.empty else None
        
        return None

    def check_signal(self, order_book_manager: OrderBookManager):
        """
        Перевіряє наявність торгового сигналу на основі поточного стану біржового стакану.
        """
        bids = order_book_manager.get_bids()
        asks = order_book_manager.get_asks()

        if bids.empty or asks.empty:
            return None

        wall_multiplier = self.params.get('wall_volume_multiplier', 10)
        activation_ticks = self.params.get('activation_distance_ticks', 15)
        
        best_bid_price = bids.index[0]
        best_ask_price = asks.index[0]

        # --- Логіка для LONG сигналу ---
        avg_bid_volume = bids['quantity'].head(20).mean()
        bid_walls = bids[bids['quantity'] > avg_bid_volume * wall_multiplier]
        
        if not bid_walls.empty:
            wall_price = bid_walls.index[0]
            wall_volume = bid_walls.iloc[0]['quantity']
            
            price_distance_to_wall = best_ask_price - wall_price
            tick_size = self._get_tick_size(bids.index.to_series())
            distance_in_ticks = price_distance_to_wall / tick_size if tick_size > 0 else float('inf')

            if 0 < distance_in_ticks <= activation_ticks:
                logger.info(f"[{self.strategy_id}] Потенційний LONG сигнал. Знайдено 'стіну' на купівлю: {wall_volume:.2f} @ {wall_price}. Поточна ціна за {distance_in_ticks:.1f} тіків.")
                return {
                    'signal_type': 'Long',
                    'wall_price': wall_price
                }

        # --- Логіка для SHORT сигналу ---
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
                return {
                    'signal_type': 'Short',
                    'wall_price': wall_price
                }

        return None

    def calculate_sl_tp(self, entry_price: float, signal_type: str, order_book_manager: OrderBookManager, tick_size: float):
        """
        Розраховує Stop-Loss та Take-Profit.
        SL - як % від ціни входу.
        TP - динамічно, на основі протилежної стіни, або за R/R, якщо стіну не знайдено.
        """
        stop_loss_pct = self.params.get('stop_loss_pct', 0.005)
        rrr = self.params.get('risk_reward_ratio', 1.5)
        tp_offset_ticks = self.params.get('tp_offset_ticks', 10)

        # Розрахунок Stop-Loss
        if signal_type == 'Long':
            stop_loss = entry_price * (1 - stop_loss_pct)
        elif signal_type == 'Short':
            stop_loss = entry_price * (1 + stop_loss_pct)
        else:
            return None

        # Динамічний розрахунок Take-Profit
        opposite_wall_price = self._find_opposite_wall(order_book_manager, signal_type)
        if opposite_wall_price:
            logger.info(f"[{self.strategy_id}] Знайдено протилежну стіну для TP: {opposite_wall_price}")
            if signal_type == 'Long':
                take_profit = opposite_wall_price - (tp_offset_ticks * tick_size)
            else: # Short
                take_profit = opposite_wall_price + (tp_offset_ticks * tick_size)
        else:
            # Якщо протилежну стіну не знайдено, повертаємось до логіки R/R
            logger.warning(f"[{self.strategy_id}] Протилежну стіну не знайдено. Розрахунок TP за R/R = {rrr}")
            if signal_type == 'Long':
                sl_distance = entry_price - stop_loss
                take_profit = entry_price + (sl_distance * rrr)
            else: # Short
                sl_distance = stop_loss - entry_price
                take_profit = entry_price - (sl_distance * rrr)

        return {
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