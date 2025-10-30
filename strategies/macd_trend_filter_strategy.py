import pandas as pd
import pandas_ta as ta
from loguru import logger

from strategies.base_strategy import BaseStrategy
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.binance_client import BinanceClient
    from core.orderbook_manager import OrderBookManager


class MacdTrendFilterStrategy(BaseStrategy):
    """
    Стратегія "MACD Crossover + Фільтр тренду".

    Використовує MACD для визначення зміни короткострокового імпульсу,
    але дозволяє входити тільки в напрямку глобального тренду,
    який визначається 200-періодною EMA.
    """

    def __init__(self, strategy_id: str, symbol: str, params: dict):
        super().__init__(strategy_id, symbol, params)
        self.macd_fast = self.params.get('macd_fast', 12)
        self.macd_slow = self.params.get('macd_slow', 26)
        self.macd_signal = self.params.get('macd_signal', 9)
        self.ema_trend_period = self.params.get('ema_trend_period', 200)
        self.atr_period = self.params.get('atr_period', 14)
        self.sl_atr_multiplier = self.params.get('sl_atr_multiplier', 1.5)
        self.rr_ratio = self.params.get('rr_ratio', 2.0) # Хоча TP буде None, залишаємо для сумісності
        self.max_sl_percentage = self.params.get('max_sl_percentage')
        self.kline_interval = self.params.get('kline_interval', '15m')
        self.use_breakeven_sl = self.params.get('use_breakeven_sl', False)

        self.kline_limit = max(self.macd_slow + self.macd_signal, self.ema_trend_period, self.atr_period) + 5

        logger.info(f"[{self.strategy_id}] Ініціалізовано MacdTrendFilterStrategy з параметрами: {self.params}")

    async def check_signal(self, order_book_manager: 'OrderBookManager', binance_client: 'BinanceClient', dataframe: pd.DataFrame | None = None) -> dict | None:
        """
        Перевіряє наявність торгового сигналу на основі аналізу K-ліній.
        """
        if dataframe is not None:
            df = dataframe.copy()
        else:
            klines = await binance_client.client.futures_klines(symbol=self.symbol, interval=self.kline_interval, limit=self.kline_limit)
            if klines.empty or len(klines) < self.kline_limit:
                logger.warning(f"[{self.strategy_id}] Недостатньо даних K-ліній для аналізу ({len(klines)} з {self.kline_limit} потрібних).")
                return None

            df = pd.DataFrame(klines, columns=['open_time', 'open', 'high', 'low', 'close', 'volume', 'close_time',
                                               'quote_asset_volume', 'number_of_trades',
                                               'taker_buy_base_asset_volume',
                                               'taker_buy_quote_asset_volume', 'ignore'])

        # Конвертуємо колонки у числовий тип
        for col in ['open', 'high', 'low', 'close', 'volume']:
            if col not in df.columns:
                continue
            df[col] = pd.to_numeric(df[col])

        # Розрахунок індикаторів
        df.ta.macd(fast=self.macd_fast, slow=self.macd_slow, signal=self.macd_signal, append=True)
        df.ta.ema(length=self.ema_trend_period, append=True, col_names=(f'EMA_{self.ema_trend_period}',))
        df.ta.atr(length=self.atr_period, append=True, col_names=(f'ATR_{self.atr_period}',))

        df.dropna(inplace=True)
        if len(df) < 2:
            return None

        print(df.tail())
        current_candle = df.iloc[-1]
        prev_candle = df.iloc[-2]

        macd_col = f'MACD_{self.macd_fast}_{self.macd_slow}_{self.macd_signal}'
        macds_col = f'MACDs_{self.macd_fast}_{self.macd_slow}_{self.macd_signal}'
        ema_trend_col = f'EMA_{self.ema_trend_period}'
        atr_col = f'ATR_{self.atr_period}'

        # --- Умови для Long ---
        is_long_trend = current_candle['close'] > current_candle[ema_trend_col]
        macd_cross_up = prev_candle[macd_col] < prev_candle[macds_col] and current_candle[macd_col] > current_candle[macds_col]
        print(f'{is_long_trend=}, {macd_cross_up=}')

        if is_long_trend and macd_cross_up:
            logger.info(f"[{self.strategy_id}] Знайдено сигнал LONG для {self.symbol} по ціні {current_candle['close']:.4f}")
            return {
                'signal_type': 'Long',
                'entry_price': current_candle['close'],
                'atr': current_candle[atr_col],
                'dataframe': df
            }

        # --- Умови для Short ---
        is_short_trend = current_candle['close'] < current_candle[ema_trend_col]
        macd_cross_down = prev_candle[macd_col] > prev_candle[macds_col] and current_candle[macd_col] < current_candle[macds_col]

        if is_short_trend and macd_cross_down:
            logger.info(f"[{self.strategy_id}] Знайдено сигнал SHORT для {self.symbol} по ціні {current_candle['close']:.4f}")
            return {
                'signal_type': 'Short',
                'entry_price': current_candle['close'],
                'atr': current_candle[atr_col],
                'dataframe': df
            }

        return None

    def calculate_sl_tp(self, entry_price: float, signal_type: str, order_book_manager: 'OrderBookManager', tick_size: float, atr: float | None = None) -> dict | None:
        """
        Розраховує Stop-Loss та Take-Profit для сигналу.
        Для цієї стратегії Take-Profit не розраховується (None), оскільки використовується трейлінг-стоп.
        """
        if atr is None:
            logger.warning(f"[{self.strategy_id}] ATR не надано для розрахунку SL. Сигнал пропущено.")
            return None

        stop_loss_price = self._calculate_stop_loss(entry_price, signal_type, atr)
        if stop_loss_price is None:
            return None

        # Вирівнюємо SL до tick_size
        stop_loss_price = round(stop_loss_price / tick_size) * tick_size

        # Take-profit не використовується, повертаємо None
        take_profit_price = self._calculate_take_profit(entry_price, stop_loss_price, signal_type, None)

        return {'stop_loss': stop_loss_price, 'take_profit': take_profit_price}

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
        # TP буде None
        return None

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

        # --- Логіка переведення в беззбиток (Breakeven SL) ---
        breakeven_triggered = False
        if self.use_breakeven_sl:
            initial_risk_amount = abs(entry_price - position['initial_stop_loss'])
            if initial_risk_amount > 0: # Уникаємо ділення на нуль
                if signal_type == 'Long':
                    # Якщо поточна ціна пройшла 1:1 R:R від початкового ризику
                    if current_price >= entry_price + initial_risk_amount:
                        # І поточний SL вище ціни входу, або ще не пересунутий на ціну входу
                        if current_sl < entry_price:
                            new_stop_loss = entry_price
                            logger.info(f"[{self.strategy_id}] Long: SL пересунуто в беззбиток ({entry_price:.4f}).")
                            breakeven_triggered = True
                elif signal_type == 'Short':
                    # Якщо поточна ціна пройшла 1:1 R:R від початкового ризику
                    if current_price <= entry_price - initial_risk_amount:
                        # І поточний SL нижче ціни входу, або ще не пересунутий на ціну входу
                        if current_sl > entry_price:
                            new_stop_loss = entry_price
                            logger.info(f"[{self.strategy_id}] Short: SL пересунуто в беззбиток ({entry_price:.4f}).")
                            breakeven_triggered = True

        # --- Логіка трейлінг-стопу ---
        if not breakeven_triggered:
            if signal_type == 'Long':
                # Для Long позиції SL повинен рухатись тільки вгору
                potential_new_sl = current_price - (self.sl_atr_multiplier * current_atr)
                if potential_new_sl > new_stop_loss: # Порівнюємо з new_stop_loss, який міг бути оновлений до беззбитку
                    new_stop_loss = potential_new_sl
                    logger.info(f"[{self.strategy_id}] Long: SL оновлено з {current_sl:.4f} на {new_stop_loss:.4f} (поточна ціна: {current_price:.4f}, ATR: {current_atr:.4f})")
            elif signal_type == 'Short':
                # Для Short позиції SL повинен рухатись тільки вниз
                potential_new_sl = current_price + (self.sl_atr_multiplier * current_atr)
                if potential_new_sl < new_stop_loss: # Порівнюємо з new_stop_loss, який міг бути оновлений до беззбитку
                    new_stop_loss = potential_new_sl
                    logger.info(f"[{self.strategy_id}] Short: SL оновлено з {current_sl:.4f} на {new_stop_loss:.4f} (поточна ціна: {current_price:.4f}, ATR: {current_atr:.4f})")

        # Вирівнюємо до tick_size
        new_stop_loss = round(new_stop_loss / tick_size) * tick_size

        if new_stop_loss != current_sl:
            return {'command': 'UPDATE_STOP_LOSS', 'new_stop_loss': new_stop_loss}

        return None
