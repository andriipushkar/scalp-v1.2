
import pandas as pd
import pandas_ta as ta
from loguru import logger

from strategies.base_strategy import BaseStrategy
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.binance_client import BinanceClient
    from core.orderbook_manager import OrderBookManager


class EmaTrendFollowingStrategy(BaseStrategy):
    """
    Стратегія "EMA Trend Following".

    Ідентифікує тренд за допомогою перетину двох EMA (швидкої та повільної)
    та використовує RSI, нахил EMA та обсяг для фільтрації сигналів.
    Stop Loss розраховується на основі ATR.
    """

    def __init__(self, strategy_id: str, symbol: str, params: dict):
        super().__init__(strategy_id, symbol, params)
        self.fast_ema_period = self.params.get('fast_ema_period', 20)
        self.slow_ema_period = self.params.get('slow_ema_period', 50)
        self.rsi_period = self.params.get('rsi_period', 14)
        self.volume_ma_period = self.params.get('volume_ma_period', 20)
        self.atr_period = self.params.get('atr_period', 14)
        self.sl_atr_multiplier = self.params.get('sl_atr_multiplier', 1.5)
        self.rr_ratio = self.params.get('rr_ratio', 2.0)
        self.max_sl_percentage = self.params.get('max_sl_percentage')
        self.kline_interval = self.params.get('kline_interval', '15m')
        self.tp_method = self.params.get('tp_method', 'rr_ratio') # 'rr_ratio' or 'local_extremum'
        self.use_candle_patterns = self.params.get('use_candle_patterns', True)
        self.use_rsi_filter = self.params.get('use_rsi_filter', True)
        self.use_volume_filter = self.params.get('use_volume_filter', True)
        self.adx_period = self.params.get('adx_period', 14)
        self.adx_threshold = self.params.get('adx_threshold', 25)
        self.use_adx_filter = self.params.get('use_adx_filter', True)

        self.use_pullback_entry = self.params.get('use_pullback_entry', False)
        self.pullback_ema_type = self.params.get('pullback_ema_type', 'fast')  # 'fast' or 'slow'
        self.pullback_tolerance_pct = self.params.get('pullback_tolerance_pct', 0.001)  # 0.1%
        # 'close', 'low' (для Long), 'high' (для Short)
        self.pullback_candle_part = self.params.get('pullback_candle_part', 'close')

        self.kline_limit = max(self.slow_ema_period, self.atr_period,
                               self.adx_period) + 5  # Беремо трохи більше даних для розрахунків

        logger.info(
            f"[{self.strategy_id}] Ініціалізовано EmaTrendFollowingStrategy з параметрами: {self.params}")

    async def check_signal(self, order_book_manager: 'OrderBookManager', binance_client: 'BinanceClient',
                           dataframe: pd.DataFrame | None = None) -> dict | None:
        """
        Перевіряє наявність торгового сигналу на основі аналізу K-ліній.
        """
        if dataframe is not None:
            df = dataframe.copy()  # Створюємо копію, щоб уникнути SettingWithCopyWarning
        else:
            klines = await binance_client.client.futures_klines(symbol=self.symbol, interval=self.kline_interval,
                                                                limit=self.kline_limit)
            if klines.empty or len(klines) < self.kline_limit:
                logger.warning(
                    f"[{self.strategy_id}] Недостатньо даних K-ліній для аналізу ({len(klines)} з {self.kline_limit} потрібних).")
                return None

            df = pd.DataFrame(klines, columns=['open_time', 'Open', 'High', 'Low', 'Close', 'Volume', 'close_time',
                                               'quote_asset_volume', 'number_of_trades',
                                               'taker_buy_base_asset_volume',
                                               'taker_buy_quote_asset_volume', 'ignore'])

        # Конвертуємо колонки у числовий тип
        for col in ['Open', 'High', 'Low', 'Close', 'Volume']:
            if col not in df.columns:
                continue
            df[col] = pd.to_numeric(df[col])

        # Розрахунок індикаторів за допомогою pandas-ta, якщо вони ще не розраховані
        if f'EMA_{self.fast_ema_period}' not in df.columns:
            df.ta.ema(length=self.fast_ema_period, append=True, col_names=(f'EMA_{self.fast_ema_period}',))
        if f'EMA_{self.slow_ema_period}' not in df.columns:
            df.ta.ema(length=self.slow_ema_period, append=True, col_names=(f'EMA_{self.slow_ema_period}',))
        if self.use_rsi_filter and f'RSI_{self.rsi_period}' not in df.columns:
            df.ta.rsi(length=self.rsi_period, append=True, col_names=(f'RSI_{self.rsi_period}',))
        if self.use_volume_filter and f'VOLUME_MA_{self.volume_ma_period}' not in df.columns:
            df.ta.sma(close=df['volume'], length=self.volume_ma_period, append=True,
                      col_names=(f'VOLUME_MA_{self.volume_ma_period}',))
        if f'ATR_{self.atr_period}' not in df.columns:
            df.ta.atr(length=self.atr_period, append=True, col_names=(f'ATR_{self.atr_period}',))
        if self.use_adx_filter and f'ADX_{self.adx_period}' not in df.columns:
            df.ta.adx(length=self.adx_period, append=True,
                      col_names=(f'ADX_{self.adx_period}', f'DMP_{self.adx_period}', f'DMN_{self.adx_period}',
                                 f'ADXR_{self.adx_period}'))

        # Видаляємо рядки з NaN після розрахунку індикаторів
        df.dropna(inplace=True)
        if len(df) < 2:
            return None  # Потрібно мінімум 2 рядки для порівняння

        # Поточна (закрита) та попередня свічки
        current_candle = df.iloc[-1]
        prev_candle = df.iloc[-2]

        # --- Фільтр ADX ---
        is_adx_trending = not self.use_adx_filter or current_candle[f'ADX_{self.adx_period}'] > self.adx_threshold
        if not is_adx_trending:
            logger.debug(
                f"[{self.strategy_id}] Сигнал відхилено через низький ADX ({current_candle[f'ADX_{self.adx_period}']:.2f} < {self.adx_threshold}).")
            return None

        # --- Умови для Long ---
        is_golden_cross = prev_candle[f'EMA_{self.fast_ema_period}'] < prev_candle[f'EMA_{self.slow_ema_period}'] and \
                          current_candle[f'EMA_{self.fast_ema_period}'] > current_candle[f'EMA_{self.slow_ema_period}']

        is_upward_slope = current_candle[f'EMA_{self.fast_ema_period}'] > prev_candle[f'EMA_{self.fast_ema_period}'] and \
                          current_candle[f'EMA_{self.slow_ema_period}'] > prev_candle[f'EMA_{self.slow_ema_period}']

        is_momentum_strong = not self.use_rsi_filter or current_candle[f'RSI_{self.rsi_period}'] > 50
        is_volume_confirmed = not self.use_volume_filter or current_candle['volume'] >= current_candle[
            f'VOLUME_MA_{self.volume_ma_period}']

        if is_golden_cross and is_upward_slope and is_momentum_strong and is_volume_confirmed:
            if self.use_pullback_entry:
                pullback_ema = current_candle[
                    f'EMA_{self.fast_ema_period}'] if self.pullback_ema_type == 'fast' else current_candle[
                    f'EMA_{self.slow_ema_period}']
                pullback_lower_bound = pullback_ema * (1 - self.pullback_tolerance_pct)
                pullback_upper_bound = pullback_ema * (1 + self.pullback_tolerance_pct)

                # Визначаємо, яку частину свічки перевіряти
                price_to_check = current_candle['Close']  # Default
                if self.pullback_candle_part == 'low':
                    price_to_check = current_candle['Low']
                elif self.pullback_candle_part == 'high':
                    price_to_check = current_candle['High']

                # Перевірка відкату: для Long очікуємо, що ціна (low або close) торкнеться EMA
                if not (pullback_lower_bound <= price_to_check <= pullback_upper_bound):
                    logger.debug(
                        f"[{self.strategy_id}] Long сигнал відхилено: ціна не на відкаті до {self.pullback_ema_type.upper()} EMA "
                        f"(перевірка по {self.pullback_candle_part}, ціна: {price_to_check:.4f}, діапазон: {pullback_lower_bound:.4f}-{pullback_upper_bound:.4f}).")
                    return None

            logger.info(
                f"[{self.strategy_id}] Знайдено сигнал LONG для {self.symbol} по ціні {current_candle['Close']:.4f}")
            return {
                'signal_type': 'Long',
                'entry_price': current_candle['Close'],
                'atr': current_candle[f'ATR_{self.atr_period}'],
                'dataframe': df
            }

        # --- Умови для Short ---
        is_death_cross = prev_candle[f'EMA_{self.fast_ema_period}'] > prev_candle[f'EMA_{self.slow_ema_period}'] and \
                         current_candle[f'EMA_{self.fast_ema_period}'] < current_candle[f'EMA_{self.slow_ema_period}']

        is_downward_slope = current_candle[f'EMA_{self.fast_ema_period}'] < prev_candle[
            f'EMA_{self.fast_ema_period}'] and \
                            current_candle[f'EMA_{self.slow_ema_period}'] < prev_candle[
                                f'EMA_{self.slow_ema_period}']

        is_momentum_weak = not self.use_rsi_filter or current_candle[f'RSI_{self.rsi_period}'] < 50

        if is_death_cross and is_downward_slope and is_momentum_weak and (
                not self.use_volume_filter or is_volume_confirmed):
            if self.use_pullback_entry:
                pullback_ema = current_candle[
                    f'EMA_{self.fast_ema_period}'] if self.pullback_ema_type == 'fast' else current_candle[
                    f'EMA_{self.slow_ema_period}']
                pullback_lower_bound = pullback_ema * (1 - self.pullback_tolerance_pct)
                pullback_upper_bound = pullback_ema * (1 + self.pullback_tolerance_pct)

                # Визначаємо, яку частину свічки перевіряти
                price_to_check = current_candle['Close']  # Default
                if self.pullback_candle_part == 'high':
                    price_to_check = current_candle['High']
                elif self.pullback_candle_part == 'low':
                    price_to_check = current_candle['Low']

                # Перевірка відкату: для Short очікуємо, що ціна (high або close) торкнеться EMA
                if not (pullback_lower_bound <= price_to_check <= pullback_upper_bound):
                    logger.debug(
                        f"[{self.strategy_id}] Short сигнал відхилено: ціна не на відкаті до {self.pullback_ema_type.upper()} EMA "
                        f"(перевірка по {self.pullback_candle_part}, ціна: {price_to_check:.4f}, діапазон: {pullback_lower_bound:.4f}-{pullback_upper_bound:.4f}).")
                    return None

            logger.info(
                f"[{self.strategy_id}] Знайдено сигнал SHORT для {self.symbol} по ціні {current_candle['Close']:.4f}")
            return {
                'signal_type': 'Short',
                'entry_price': current_candle['Close'],
                'atr': current_candle[f'ATR_{self.atr_period}'],
                'dataframe': df
            }

        return None

    def _calculate_stop_loss(self, entry_price: float, signal_type: str, atr: float) -> float | None:
        """Розраховує ціну Stop-Loss на основі ATR та максимального відсотка."""
        if signal_type == 'Long':
            stop_loss_price = entry_price - (self.sl_atr_multiplier * atr)
        elif signal_type == 'Short':
            stop_loss_price = entry_price + (self.sl_atr_multiplier * atr)
        else:
            return None

        if self.max_sl_percentage is not None:
            if signal_type == 'Long':
                max_sl_price = entry_price * (1 - self.max_sl_percentage)
                if stop_loss_price < max_sl_price:
                    logger.warning(f"[{self.strategy_id}] SL для Long скориговано: вхід={entry_price:.4f}, розрах.SL={stop_loss_price:.4f} < макс.SL={max_sl_price:.4f} ({self.max_sl_percentage:.2%}). Встановлено макс. SL.")
                    stop_loss_price = max_sl_price
            elif signal_type == 'Short':
                max_sl_price = entry_price * (1 + self.max_sl_percentage)
                if stop_loss_price > max_sl_price:
                    logger.warning(f"[{self.strategy_id}] SL для Short скориговано: вхід={entry_price:.4f}, розрах.SL={stop_loss_price:.4f} > макс.SL={max_sl_price:.4f} ({self.max_sl_percentage:.2%}). Встановлено макс. SL.")
                    stop_loss_price = max_sl_price
        return stop_loss_price

    def _calculate_take_profit(self, entry_price: float, stop_loss_price: float, signal_type: str, dataframe: pd.DataFrame | None) -> float | None:
        """Розраховує ціну Take-Profit на основі методу (R:R або локальний екстремум)."""
        take_profit_price = None
        if self.tp_method == 'local_extremum' and dataframe is not None and not dataframe.empty:
            lookback_period = 50
            recent_candles = dataframe.iloc[max(0, len(dataframe) - lookback_period - 1):-1]

            if not recent_candles.empty:
                if signal_type == 'Long':
                    local_high = recent_candles['high'].max()
                    if local_high > entry_price:
                        take_profit_price = local_high
                        logger.debug(f"[{self.strategy_id}] TP розраховано за локальним максимумом: {take_profit_price:.4f}")
                elif signal_type == 'Short':
                    local_low = recent_candles['low'].min()
                    if local_low < entry_price:
                        take_profit_price = local_low
                        logger.debug(f"[{self.strategy_id}] TP розраховано за локальним мінімумом: {take_profit_price:.4f}")

        if take_profit_price is None:
            logger.debug(f"[{self.strategy_id}] Не вдалося знайти локальний екстремум або метод 'rr_ratio', TP розраховується за R:R = {self.rr_ratio}")
            if signal_type == 'Long':
                risk_amount = entry_price - stop_loss_price
                take_profit_price = entry_price + (self.rr_ratio * risk_amount)
            else:  # Short
                risk_amount = stop_loss_price - entry_price
                take_profit_price = entry_price - (self.rr_ratio * risk_amount)
        return take_profit_price

    def calculate_sl_tp(self, entry_price: float, signal_type: str, order_book_manager: 'OrderBookManager', tick_size: float, **kwargs) -> dict | None:
        """
        Розраховує Stop-Loss та Take-Profit, викликаючи окремі методи.
        """
        atr = kwargs.get('atr')
        dataframe = kwargs.get('dataframe')

        if not atr:
            logger.error(f"[{self.strategy_id}] Неможливо розрахувати SL/TP: не передано значення ATR.")
            return None

        stop_loss_price = self._calculate_stop_loss(entry_price, signal_type, atr)
        if stop_loss_price is None:
            return None

        take_profit_price = self._calculate_take_profit(entry_price, stop_loss_price, signal_type, dataframe)
        if take_profit_price is None:
            # Це не повинно трапитись, оскільки R:R є запасним варіантом
            logger.error(f"[{self.strategy_id}] Не вдалося розрахувати Take-Profit.")
            return None

        # Вирівнюємо до tick_size
        stop_loss_price = round(stop_loss_price / tick_size) * tick_size
        take_profit_price = round(take_profit_price / tick_size) * tick_size

        logger.debug(f"[{self.strategy_id}] Розраховано для {signal_type} @ {entry_price:.4f}: SL={stop_loss_price:.4f}, TP={take_profit_price:.4f}")
        return {'stop_loss': stop_loss_price, 'take_profit': take_profit_price}

    async def analyze_and_adjust(self, position: dict, order_book_manager: 'OrderBookManager', binance_client: 'BinanceClient', dataframe: pd.DataFrame | None = None) -> dict | None:
        """
        Реалізує логіку трейлінг-стопу для відкритої позиції.
        """
        current_sl = position['stop_loss']
        entry_price = position['entry_price']
        signal_type = position['side']
        current_price = order_book_manager.get_current_price(self.symbol)
        tick_size = order_book_manager.get_tick_size(self.symbol)

        if dataframe is None:
            klines = await binance_client.client.futures_klines(symbol=self.symbol, interval=self.kline_interval, limit=self.kline_limit)
            if not klines or len(klines) < self.kline_limit:
                logger.warning(f"[{self.strategy_id}] Недостатньо даних K-ліній для аналізу та коригування ({len(klines)} з {self.kline_limit} потрібних).")
                return None
            df = pd.DataFrame(klines, columns=['open_time', 'open', 'high', 'low', 'close', 'volume', 'close_time',
                                               'quote_asset_volume', 'number_of_trades', 'taker_buy_base_asset_volume',
                                               'taker_buy_quote_asset_volume', 'ignore'])
            for col in ['open', 'high', 'low', 'close', 'volume']:
                if col not in df.columns:
                    continue
                df[col] = pd.to_numeric(df[col])
        else:
            df = dataframe.copy()

        # Розрахунок ATR для поточної свічки
        if f'ATR_{self.atr_period}' not in df.columns:
            df.ta.atr(length=self.atr_period, append=True, col_names=(f'ATR_{self.atr_period}',))
        df.dropna(inplace=True)

        if df.empty:
            logger.warning(f"[{self.strategy_id}] DataFrame порожній після розрахунку ATR. Неможливо розрахувати трейлінг-стоп.")
            return None

        current_atr = df.iloc[-1][f'ATR_{self.atr_period}']
        new_stop_loss = current_sl

        if signal_type == 'Long':
            # Для Long позиції SL повинен рухатись тільки вгору
            potential_new_sl = current_price - (self.sl_atr_multiplier * current_atr)
            if potential_new_sl > current_sl:
                new_stop_loss = potential_new_sl
                logger.info(f"[{self.strategy_id}] Long: SL оновлено з {current_sl:.4f} на {new_stop_loss:.4f} (поточна ціна: {current_price:.4f}, ATR: {current_atr:.4f})")
        elif signal_type == 'Short':
            # Для Short позиції SL повинен рухатись тільки вниз
            potential_new_sl = current_price + (self.sl_atr_multiplier * current_atr)
            if potential_new_sl < current_sl:
                new_stop_loss = potential_new_sl
                logger.info(f"[{self.strategy_id}] Short: SL оновлено з {current_sl:.4f} на {new_stop_loss:.4f} (поточна ціна: {current_price:.4f}, ATR: {current_atr:.4f})")

        # Вирівнюємо до tick_size
        new_stop_loss = round(new_stop_loss / tick_size) * tick_size

        if new_stop_loss != current_sl:
            return {'command': 'UPDATE_STOP_LOSS', 'new_stop_loss': new_stop_loss}

        return None


