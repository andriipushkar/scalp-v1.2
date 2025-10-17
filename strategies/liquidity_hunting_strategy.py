from strategies.base_strategy import BaseStrategy
from core.orderbook_manager import OrderBookManager
import pandas as pd
from loguru import logger

class LiquidityHuntingStrategy(BaseStrategy):
    """
    Стратегія "Полювання на ліквідність".
    
    Логіка стратегії полягає в пошуку аномально великих лімітних ордерів ("стін")
    в біржовому стакані та виставленні ордеру на вхід безпосередньо перед цією стіною,
    розраховуючи на відскок ціни. Stop-Loss виставляється за стіною.
    """
    def __init__(self, strategy_id: str, symbol: str, params: dict):
        """Ініціалізує стратегію з її унікальним ID, символом та параметрами з конфігурації."""
        super().__init__(strategy_id, symbol, params)
        logger.info(f"[{self.strategy_id}] Ініціалізовано LiquidityHuntingStrategy з параметрами: {self.params}")

    def _find_opposite_wall(self, order_book_manager: OrderBookManager, signal_type: str) -> float | None:
        """
        Допоміжний метод для пошуку ціни найближчої "стіни" ліквідності 
        у протилежному напрямку від поточної позиції. Використовується для динамічного
        визначення Take-Profit.
        """
        wall_multiplier = self.params.get('wall_volume_multiplier', 10)

        if signal_type == 'Long': # Якщо ми в лонзі, шукаємо стіну на продаж (в асках)
            asks = order_book_manager.get_asks()
            if asks.empty: return None
            avg_ask_volume = asks['quantity'].head(20).mean()
            ask_walls = asks[asks['quantity'] > avg_ask_volume * wall_multiplier]
            return ask_walls.index[0] if not ask_walls.empty else None
        
        elif signal_type == 'Short': # Якщо ми в шорті, шукаємо стіну на покупку (в бідах)
            bids = order_book_manager.get_bids()
            if bids.empty: return None
            avg_bid_volume = bids['quantity'].head(20).mean()
            bid_walls = bids[bids['quantity'] > avg_bid_volume * wall_multiplier]
            return bid_walls.index[0] if not bid_walls.empty else None
        
        return None

    def check_signal(self, order_book_manager: OrderBookManager) -> dict | None:
        """
        Перевіряє наявність торгового сигналу на основі поточного стану біржового стакану.
        """
        bids = order_book_manager.get_bids()
        asks = order_book_manager.get_asks()

        if bids.empty or asks.empty:
            return None # Немає даних для аналізу

        # Завантажуємо параметри з конфігурації
        wall_multiplier = self.params.get('wall_volume_multiplier', 10)
        activation_ticks = self.params.get('activation_distance_ticks', 15)
        tick_size = self._get_tick_size(bids.index.to_series()) # Визначаємо тік
        if tick_size == 0: return None
        
        best_ask_price = asks.index[0]

        # --- Логіка для LONG сигналу (шукаємо стіну на покупку) ---
        avg_bid_volume = bids['quantity'].head(20).mean() # Середній обсяг на 20 кращих рівнях
        bid_walls = bids[bids['quantity'] > avg_bid_volume * wall_multiplier]
        
        if not bid_walls.empty:
            wall_price = bid_walls.index[0] # Ціна найбільшої стіни
            wall_volume = bid_walls.iloc[0]['quantity']
            
            price_distance_to_wall = best_ask_price - wall_price
            distance_in_ticks = price_distance_to_wall / tick_size

            # Перевіряємо, чи ціна знаходиться достатньо близько до стіни
            if 0 < distance_in_ticks <= activation_ticks:
                logger.debug(f"[{self.strategy_id}] Потенційний LONG. Стіна: {wall_volume:.2f} @ {wall_price}. Дистанція: {distance_in_ticks:.1f} тіків.")
                return {
                    'signal_type': 'Long',
                    'wall_price': wall_price
                }

        # --- Логіка для SHORT сигналу (шукаємо стіну на продаж) ---
        best_bid_price = bids.index[0]
        avg_ask_volume = asks['quantity'].head(20).mean()
        ask_walls = asks[asks['quantity'] > avg_ask_volume * wall_multiplier]

        if not ask_walls.empty:
            wall_price = ask_walls.index[0]
            wall_volume = ask_walls.iloc[0]['quantity']

            price_distance_to_wall = wall_price - best_bid_price
            distance_in_ticks = price_distance_to_wall / tick_size

            if 0 < distance_in_ticks <= activation_ticks:
                logger.debug(f"[{self.strategy_id}] Потенційний SHORT. Стіна: {wall_volume:.2f} @ {wall_price}. Дистанція: {distance_in_ticks:.1f} тіків.")
                return {
                    'signal_type': 'Short',
                    'wall_price': wall_price
                }

        return None # Сигналу не знайдено

    def calculate_sl_tp(self, entry_price: float, signal_type: str, order_book_manager: OrderBookManager, tick_size: float) -> dict | None:
        """
        Розраховує Stop-Loss та Take-Profit для угоди.
        - Stop-Loss розраховується як фіксований відсоток від ціни входу.
        - Take-Profit розраховується динамічно (пошук протилежної стіни) або на основі R/R.
        """
        # --- Розрахунок Stop-Loss ---
        sl_pct = self.params.get('stop_loss_pct', 0.005) # 0.5% за замовчуванням
        if signal_type == 'Long':
            stop_loss = entry_price * (1 - sl_pct)
        elif signal_type == 'Short':
            stop_loss = entry_price * (1 + sl_pct)
        else:
            return None

        # --- Динамічний розрахунок Take-Profit ---
        opposite_wall_price = self._find_opposite_wall(order_book_manager, signal_type)
        tp_offset_ticks = self.params.get('tp_offset_ticks', 10)

        if opposite_wall_price:
            # Якщо знайдено протилежну стіну, ставимо TP перед нею
            logger.info(f"[{self.strategy_id}] Знайдено протилежну стіну для TP: {opposite_wall_price}")
            if signal_type == 'Long':
                take_profit = opposite_wall_price - (tp_offset_ticks * tick_size)
            else: # Short
                take_profit = opposite_wall_price + (tp_offset_ticks * tick_size)
        else:
            # Якщо стіну не знайдено, розраховуємо TP на основі співвідношення ризику до прибутку (R/R)
            rrr = self.params.get('risk_reward_ratio', 1.5)
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
        """Допоміжна функція для розрахунку мінімального кроку ціни (тіку) на основі даних стакану."""
        if len(price_series) < 2:
            return 0.0 # Неможливо розрахувати
        # Знаходимо мінімальну різницю між сусідніми рівнями цін
        tick = price_series.diff().abs().min()
        return tick if pd.notna(tick) and tick > 0 else 0.0
