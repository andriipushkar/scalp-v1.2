from .base_strategy import BaseStrategy
from core.orderbook_manager import OrderBookManager
from loguru import logger
import pandas as pd

class DynamicOrderbookStrategy(BaseStrategy):
    """
    Стратегія, що використовує аналіз стакану для динамічного управління позицією,
    включаючи трейлінг-стоп та завчасне закриття.
    """

    def __init__(self, strategy_id: str, symbol: str, params: dict):
        super().__init__(strategy_id, symbol, params)
        self.stop_loss_percent = params.get("stop_loss_percent", 1.5)
        self.initial_tp_min_search_percent = params.get("initial_tp_min_search_percent", 1.0)
        self.initial_tp_search_percent = params.get("initial_tp_search_percent", 3.0)
        self.trailing_sl_distance_percent = params.get("trailing_sl_distance_percent", 1.0)
        self.pre_emptive_close_threshold_mult = params.get("pre_emptive_close_threshold_mult", 2.0)
        self.wall_volume_multiplier = params.get('wall_volume_multiplier', 10)
        self.activation_distance_ticks = params.get('activation_distance_ticks', 15)
        self.max_spread_bps = params.get('max_spread_bps', 5)
        self.min_wall_volume = params.get('min_wall_volume', 100) # New parameter
        logger.info(f"[{strategy_id}] Ініціалізовано DynamicOrderbookStrategy з параметрами: {params}")

    def _get_tick_size(self, price_series: pd.Series) -> float:
        """Розраховує мінімальний крок ціни (тік) на основі даних стакану."""
        if len(price_series) < 2:
            return 0.01 # Значення за замовчуванням, якщо не вдалося розрахувати
        # Знаходимо мінімальну різницю між сусідніми рівнями цін
        tick = price_series.diff().abs().min()
        return tick if pd.notna(tick) and tick > 0 else 0.01

    def check_signal(self, orderbook_manager: OrderBookManager) -> dict | None:
        """
        Перевіряє наявність торгового сигналу на основі поточного стану біржового стакану.
        """
        bids = orderbook_manager.get_bids()
        asks = orderbook_manager.get_asks()

        if bids.empty or asks.empty:
            return None

        best_bid_price = bids.index[0]
        best_ask_price = asks.index[0]

        # --- Фільтр по спреду ---
        spread = best_ask_price - best_bid_price
        if best_bid_price > 0: # Уникаємо ділення на нуль
            spread_bps = (spread / best_bid_price) * 10000
            if spread_bps > self.max_spread_bps:
                logger.debug(f"[{self.strategy_id}] Сигнал відхилено: спред занадто широкий ({spread_bps:.2f} bps > {self.max_spread_bps} bps).")
                return None

        # --- Логіка для LONG сигналу ---
        avg_bid_volume = bids['quantity'].head(20).mean()
        bid_walls = bids[bids['quantity'] > avg_bid_volume * self.wall_volume_multiplier]
        
        if not bid_walls.empty:
            wall_price = bid_walls.index[0]
            wall_volume = bid_walls.loc[wall_price]['quantity'] # Get volume for the specific wall price
            
            # Фільтр по мінімальному об'єму стіни
            if wall_volume < self.min_wall_volume:
                logger.debug(f"[{self.strategy_id}] Сигнал відхилено: об'єм стіни ({wall_volume}) менший за мінімальний ({self.min_wall_volume}).")
                return None

            price_distance_to_wall = best_ask_price - wall_price
            tick_size = self._get_tick_size(bids.index.to_series())
            distance_in_ticks = price_distance_to_wall / tick_size if tick_size > 0 else float('inf')

            if 0 < distance_in_ticks <= self.activation_distance_ticks:
                logger.info(f"[{self.strategy_id}] Потенційний LONG сигнал. Знайдено 'стіну' на купівлю: {wall_volume:.2f} @ {wall_price}. Поточна ціна за {distance_in_ticks:.1f} тіків.")
                return {
                    'signal_type': 'Long',
                    'wall_price': wall_price
                }

        # --- Логіка для SHORT сигналу ---
        avg_ask_volume = asks['quantity'].head(20).mean()
        ask_walls = asks[asks['quantity'] > avg_ask_volume * self.wall_volume_multiplier]

        if not ask_walls.empty:
            wall_price = ask_walls.index[0]
            wall_volume = ask_walls.loc[wall_price]['quantity'] # Get volume for the specific wall price

            # Фільтр по мінімальному об'єму стіни
            if wall_volume < self.min_wall_volume:
                logger.debug(f"[{self.strategy_id}] Сигнал відхилено: об'єм стіни ({wall_volume}) менший за мінімальний ({self.min_wall_volume}).")
                return None

            price_distance_to_wall = wall_price - best_bid_price
            tick_size = self._get_tick_size(asks.index.to_series())
            distance_in_ticks = price_distance_to_wall / tick_size if tick_size > 0 else float('inf')

            if 0 < distance_in_ticks <= self.activation_distance_ticks:
                logger.info(f"[{self.strategy_id}] Потенційний SHORT сигнал. Знайдено 'стіну' на продаж: {wall_volume:.2f} @ {wall_price}. Поточна ціна за {distance_in_ticks:.1f} тіків.")
                return {
                    'signal_type': 'Short',
                    'wall_price': wall_price
                }

        return None

    def calculate_sl_tp(self, entry_price: float, signal_type: str, order_book_manager: OrderBookManager, **kwargs) -> dict:
        """
        Розраховує SL на основі відсотка та TP на основі аналізу стакану.
        """
        if signal_type == 'Long':
            stop_loss = entry_price * (1 - self.stop_loss_percent / 100)
            
            # Пошук TP в стакані у заданому діапазоні
            min_tp_price = entry_price * (1 + self.initial_tp_min_search_percent / 100)
            max_tp_price = entry_price * (1 + self.initial_tp_search_percent / 100)
            asks = order_book_manager.get_asks()
            # Фільтруємо рівні, що знаходяться у потрібному діапазоні
            relevant_asks = asks[(asks.index >= min_tp_price) & (asks.index <= max_tp_price)]
            
            if relevant_asks.empty:
                take_profit = max_tp_price # Якщо не знайдено ліквідності, ставимо на максимальну межу
            else:
                # Знаходимо рівень з найбільшим об'ємом
                take_profit = relevant_asks['quantity'].idxmax()

        elif signal_type == 'Short':
            stop_loss = entry_price * (1 + self.stop_loss_percent / 100)

            # Пошук TP в стакані у заданому діапазоні
            min_tp_price = entry_price * (1 - self.initial_tp_search_percent / 100)
            max_tp_price = entry_price * (1 - self.initial_tp_min_search_percent / 100)
            bids = order_book_manager.get_bids()
            relevant_bids = bids[(bids.index <= max_tp_price) & (bids.index >= min_tp_price)]

            if relevant_bids.empty:
                take_profit = min_tp_price
            else:
                take_profit = relevant_bids['quantity'].idxmax()
        else:
            return {}

        logger.debug(f"[{self.strategy_id}] Розраховано SL: {stop_loss}, TP: {take_profit} для входу по {entry_price}")
        return {'stop_loss': stop_loss, 'take_profit': take_profit}

    def analyze_and_adjust(self, position: dict, orderbook_manager: OrderBookManager) -> dict | None:
        """
        Аналізує поточну позицію та ринкові дані для прийняття рішення про коригування.
        
        :return: Словник з командою ('ADJUST_TP_SL' або 'CLOSE_POSITION') або None.
        """
        side = position.get("side")
        entry_price = position.get("entry_price")
        current_sl = position.get("stop_loss")
        current_tp = position.get("take_profit")

        if side == "Long":
            current_price = orderbook_manager.get_best_bid()
            if not current_price:
                return None

            # --- 1. Логіка Trailing Stop ---
            new_sl_price = current_price * (1 - self.trailing_sl_distance_percent / 100)
            if new_sl_price > current_sl and new_sl_price > entry_price:
                # Також перераховуємо TP, щоб він рухався за ціною
                new_tp_price = current_price * (1 + self.initial_tp_search_percent / 100)
                logger.info(f"[{self.strategy_id}] Trailing SL. New SL: {new_sl_price:.4f} > Current SL: {current_sl:.4f}. New TP: {new_tp_price:.4f}")
                return {
                    "command": "ADJUST_TP_SL",
                    "stop_loss": new_sl_price,
                    "take_profit": new_tp_price
                }

            # --- 2. Логіка випереджувального закриття ---
            asks = orderbook_manager.get_asks()
            bids = orderbook_manager.get_bids()
            price_range = current_price * 0.005 # Аналізуємо тиск у стакані в межах 0.5% від ціни
            relevant_asks_vol = asks[asks.index < current_price + price_range]['quantity'].sum()
            relevant_bids_vol = bids[bids.index > current_price - price_range]['quantity'].sum()

            if relevant_bids_vol > 0 and (relevant_asks_vol / relevant_bids_vol) > self.pre_emptive_close_threshold_mult:
                logger.warning(f"[{self.strategy_id}] Pre-emptive close for LONG. Ask pressure detected. Asks: {relevant_asks_vol}, Bids: {relevant_bids_vol}")
                return {"command": "CLOSE_POSITION"}

        elif side == "Short":
            current_price = orderbook_manager.get_best_ask()
            if not current_price:
                return None

            # --- 1. Логіка Trailing Stop ---
            new_sl_price = current_price * (1 + self.trailing_sl_distance_percent / 100)
            if new_sl_price < current_sl and new_sl_price < entry_price:
                # Також перераховуємо TP, щоб він рухався за ціною
                new_tp_price = current_price * (1 - self.initial_tp_search_percent / 100)
                logger.info(f"[{self.strategy_id}] Trailing SL. New SL: {new_sl_price:.4f} < Current SL: {current_sl:.4f}. New TP: {new_tp_price:.4f}")
                return {
                    "command": "ADJUST_TP_SL",
                    "stop_loss": new_sl_price,
                    "take_profit": new_tp_price
                }

            # --- 2. Логіка випереджувального закриття ---
            asks = orderbook_manager.get_asks()
            bids = orderbook_manager.get_bids()
            price_range = current_price * 0.005
            relevant_asks_vol = asks[asks.index < current_price + price_range]['quantity'].sum()
            relevant_bids_vol = bids[bids.index > current_price - price_range]['quantity'].sum()

            if relevant_asks_vol > 0 and (relevant_bids_vol / relevant_asks_vol) > self.pre_emptive_close_threshold_mult:
                logger.warning(f"[{self.strategy_id}] Pre-emptive close for SHORT. Bid pressure detected. Bids: {relevant_bids_vol}, Asks: {relevant_asks_vol}")
                return {"command": "CLOSE_POSITION"}

        return None
