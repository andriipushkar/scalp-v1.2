from __future__ import annotations
import asyncio
from typing import TYPE_CHECKING
from .base_strategy import BaseStrategy
from core.orderbook_manager import OrderBookManager
from loguru import logger
import pandas as pd

if TYPE_CHECKING:
    from core.binance_client import BinanceClient

class DynamicOrderbookStrategy(BaseStrategy):
    """
    Стратегія, що використовує аналіз стакану, доповнений фільтрами EMA та RSI.
    """

    def __init__(self, strategy_id: str, symbol: str, params: dict):
        super().__init__(strategy_id, symbol, params)
        # Wall detection params
        self.wall_volume_multiplier = params.get('wall_volume_multiplier', 10)
        self.activation_distance_ticks = params.get('activation_distance_ticks', 15)
        self.max_spread_bps = params.get('max_spread_bps', 5)
        self.min_wall_volume = params.get('min_wall_volume', 100)

        # Indicator filter params
        self.ema_filter_enabled = params.get('ema_filter_enabled', False)
        self.ema_period = params.get('ema_period', 200)
        self.ema_timeframe = params.get('ema_timeframe', '5m')
        self.rsi_filter_enabled = params.get('rsi_filter_enabled', False)
        self.rsi_period = params.get('rsi_period', 14)
        self.rsi_timeframe = params.get('rsi_timeframe', '5m')
        self.rsi_long_threshold = params.get('rsi_long_threshold', 60)
        self.rsi_short_threshold = params.get('rsi_short_threshold', 40)

        # SL/TP and adjustment params
        self.stop_loss_percent = params.get("stop_loss_percent", 1.5)
        self.initial_tp_min_search_percent = params.get("initial_tp_min_search_percent", 1.0)
        self.initial_tp_search_percent = params.get("initial_tp_search_percent", 3.0)
        self.trailing_sl_distance_percent = params.get("trailing_sl_distance_percent", 1.0)
        self.pre_emptive_close_threshold_mult = params.get("pre_emptive_close_threshold_mult", 2.0)
        
        self.klines_cache = {}
        logger.info(f"[{strategy_id}] Ініціалізовано DynamicOrderbookStrategy з параметрами: {params}")

    def _get_tick_size(self, price_series: pd.Series) -> float:
        if len(price_series) < 2:
            return 0.01
        tick = price_series.diff().abs().min()
        return tick if pd.notna(tick) and tick > 0 else 0.01

    def _calculate_rsi(self, series: pd.Series, period: int) -> float:
        delta = series.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        return rsi.iloc[-1]

    def _calculate_ema(self, series: pd.Series, period: int) -> float:
        ema = series.ewm(span=period, adjust=False).mean()
        return ema.iloc[-1]

    async def _get_historical_data(self, symbol: str, timeframe: str, limit: int, binance_client: 'BinanceClient') -> pd.DataFrame | None:
        cache_key = f"{symbol}_{timeframe}_{limit}"
        now = asyncio.get_event_loop().time()

        if cache_key in self.klines_cache and (now - self.klines_cache[cache_key]['timestamp']) < 60:
            return self.klines_cache[cache_key]['data']

        try:
            klines = await binance_client.client.futures_klines(symbol=symbol, interval=timeframe, limit=limit)
            df = pd.DataFrame(klines, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'close_time',
                                             'quote_asset_volume', 'number_of_trades', 'taker_buy_base_asset_volume',
                                             'taker_buy_quote_asset_volume', 'ignore'])
            df['close'] = pd.to_numeric(df['close'])
            self.klines_cache[cache_key] = {'timestamp': now, 'data': df}
            return df
        except Exception as e:
            logger.error(f"[{self.strategy_id}] Не вдалося отримати історичні дані для {symbol}: {e}")
            return None

    def _check_wall_signal(self, orderbook_manager: OrderBookManager) -> dict | None:
        bids = orderbook_manager.get_bids()
        asks = orderbook_manager.get_asks()
        if bids.empty or asks.empty:
            return None

        best_bid_price = bids.index[0]
        best_ask_price = asks.index[0]

        spread = best_ask_price - best_bid_price
        if best_bid_price > 0:
            spread_bps = (spread / best_bid_price) * 10000
            if spread_bps > self.max_spread_bps:
                return None

        avg_bid_volume = bids['quantity'].head(20).mean()
        bid_walls = bids[bids['quantity'] > avg_bid_volume * self.wall_volume_multiplier]
        if not bid_walls.empty:
            wall_price = bid_walls.index[0]
            wall_volume = bid_walls.loc[wall_price]['quantity']
            if wall_volume >= self.min_wall_volume:
                price_distance_to_wall = best_ask_price - wall_price
                tick_size = self._get_tick_size(bids.index.to_series())
                distance_in_ticks = price_distance_to_wall / tick_size if tick_size > 0 else float('inf')
                if 0 < distance_in_ticks <= self.activation_distance_ticks:
                    return {'signal_type': 'Long', 'wall_price': wall_price}

        avg_ask_volume = asks['quantity'].head(20).mean()
        ask_walls = asks[asks['quantity'] > avg_ask_volume * self.wall_volume_multiplier]
        if not ask_walls.empty:
            wall_price = ask_walls.index[0]
            wall_volume = ask_walls.loc[wall_price]['quantity']
            if wall_volume >= self.min_wall_volume:
                price_distance_to_wall = wall_price - best_bid_price
                tick_size = self._get_tick_size(asks.index.to_series())
                distance_in_ticks = price_distance_to_wall / tick_size if tick_size > 0 else float('inf')
                if 0 < distance_in_ticks <= self.activation_distance_ticks:
                    return {'signal_type': 'Short', 'wall_price': wall_price}
        return None

    async def check_signal(self, orderbook_manager: OrderBookManager, binance_client: 'BinanceClient') -> dict | None:
        wall_signal = self._check_wall_signal(orderbook_manager)
        if not wall_signal:
            return None

        logger.info(f"[{self.strategy_id}] Потенційний сигнал від стіни: {wall_signal}. Перевірка фільтрів...")

        # Якщо всі фільтри вимкнені, повертаємо сигнал одразу
        if not self.ema_filter_enabled and not self.rsi_filter_enabled:
            return wall_signal

        # --- Отримання даних та розрахунок індикаторів ---
        limit = max(self.ema_period, self.rsi_period) + 1
        timeframe = self.ema_timeframe if self.ema_filter_enabled else self.rsi_timeframe
        klines_df = await self._get_historical_data(self.symbol, timeframe, limit, binance_client)
        if klines_df is None or klines_df.empty:
            logger.warning(f"[{self.strategy_id}] Не вдалося отримати дані для фільтрів. Сигнал пропущено.")
            return None

        current_price = klines_df['close'].iloc[-1]

        # --- EMA Filter ---
        if self.ema_filter_enabled:
            ema_value = self._calculate_ema(klines_df['close'], self.ema_period)
            if wall_signal['signal_type'] == 'Long' and current_price < ema_value:
                logger.debug(f"[{self.strategy_id}] Сигнал LONG відхилено фільтром EMA. Ціна {current_price} < EMA({self.ema_period}) {ema_value:.2f}")
                return None
            if wall_signal['signal_type'] == 'Short' and current_price > ema_value:
                logger.debug(f"[{self.strategy_id}] Сигнал SHORT відхилено фільтром EMA. Ціна {current_price} > EMA({self.ema_period}) {ema_value:.2f}")
                return None

        # --- RSI Filter ---
        if self.rsi_filter_enabled:
            rsi_value = self._calculate_rsi(klines_df['close'], self.rsi_period)
            if wall_signal['signal_type'] == 'Long' and rsi_value > self.rsi_long_threshold:
                logger.debug(f"[{self.strategy_id}] Сигнал LONG відхилено фільтром RSI. RSI({self.rsi_period}) {rsi_value:.2f} > {self.rsi_long_threshold}")
                return None
            if wall_signal['signal_type'] == 'Short' and rsi_value < self.rsi_short_threshold:
                logger.debug(f"[{self.strategy_id}] Сигнал SHORT відхилено фільтром RSI. RSI({self.rsi_period}) {rsi_value:.2f} < {self.rsi_short_threshold}")
                return None

        logger.success(f"[{self.strategy_id}] Сигнал {wall_signal['signal_type']} пройшов усі фільтри.")
        return wall_signal

    def calculate_sl_tp(self, entry_price: float, signal_type: str, order_book_manager: OrderBookManager, **kwargs) -> dict:
        if signal_type == 'Long':
            stop_loss = entry_price * (1 - self.stop_loss_percent / 100)
            min_tp_price = entry_price * (1 + self.initial_tp_min_search_percent / 100)
            max_tp_price = entry_price * (1 + self.initial_tp_search_percent / 100)
            asks = order_book_manager.get_asks()
            relevant_asks = asks[(asks.index >= min_tp_price) & (asks.index <= max_tp_price)]
            if relevant_asks.empty:
                take_profit = max_tp_price
            else:
                take_profit = relevant_asks['quantity'].idxmax()
        elif signal_type == 'Short':
            stop_loss = entry_price * (1 + self.stop_loss_percent / 100)
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
        return {'stop_loss': stop_loss, 'take_profit': take_profit}

    def analyze_and_adjust(self, position: dict, orderbook_manager: OrderBookManager) -> dict | None:
        side = position.get("side")
        entry_price = position.get("entry_price")
        current_sl = position.get("stop_loss")

        if side == "Long":
            current_price = orderbook_manager.get_best_bid()
            if not current_price:
                return None

            new_sl_price = current_price * (1 - self.trailing_sl_distance_percent / 100)
            if new_sl_price > current_sl and new_sl_price > entry_price:
                new_tp_price = current_price * (1 + self.initial_tp_search_percent / 100)
                return {"command": "ADJUST_TP_SL", "stop_loss": new_sl_price, "take_profit": new_tp_price}

            asks = orderbook_manager.get_asks()
            bids = orderbook_manager.get_bids()
            price_range = current_price * 0.005
            relevant_asks_vol = asks[asks.index < current_price + price_range]['quantity'].sum()
            relevant_bids_vol = bids[bids.index > current_price - price_range]['quantity'].sum()
            if relevant_bids_vol > 0 and (relevant_asks_vol / relevant_bids_vol) > self.pre_emptive_close_threshold_mult:
                return {"command": "CLOSE_POSITION", "reason": "Ask pressure detected"}

        elif side == "Short":
            current_price = orderbook_manager.get_best_ask()
            if not current_price:
                return None

            new_sl_price = current_price * (1 + self.trailing_sl_distance_percent / 100)
            if new_sl_price < current_sl and new_sl_price < entry_price:
                new_tp_price = current_price * (1 - self.initial_tp_search_percent / 100)
                return {"command": "ADJUST_TP_SL", "stop_loss": new_sl_price, "take_profit": new_tp_price}

            asks = orderbook_manager.get_asks()
            bids = orderbook_manager.get_bids()
            price_range = current_price * 0.005
            relevant_asks_vol = asks[asks.index < current_price + price_range]['quantity'].sum()
            relevant_bids_vol = bids[bids.index > current_price - price_range]['quantity'].sum()
            if relevant_asks_vol > 0 and (relevant_bids_vol / relevant_asks_vol) > self.pre_emptive_close_threshold_mult:
                return {"command": "CLOSE_POSITION", "reason": "Bid pressure detected"}

        return None