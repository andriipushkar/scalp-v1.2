
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
        self.kline_interval = self.params.get('kline_interval', '15m')
        self.tp_method = self.params.get('tp_method', 'rr_ratio') # 'rr_ratio' or 'local_extremum'
        self.kline_limit = self.slow_ema_period + 5 # Беремо трохи більше даних для розрахунків
        self.use_candle_patterns = self.params.get('use_candle_patterns', True)
        self.use_rsi_filter = self.params.get('use_rsi_filter', True)
        self.use_volume_filter = self.params.get('use_volume_filter', True)

        logger.info(f"[{self.strategy_id}] Ініціалізовано EmaTrendFollowingStrategy з параметрами: {self.params}")

    async def check_signal(self, order_book_manager: 'OrderBookManager', binance_client: 'BinanceClient', dataframe: pd.DataFrame | None = None) -> dict | None:
        """
        Перевіряє наявність торгового сигналу на основі аналізу K-ліній.
        """
        if dataframe is not None:
            df = dataframe.copy() # Створюємо копію, щоб уникнути SettingWithCopyWarning
        else:
            klines = await binance_client.client.futures_klines(symbol=self.symbol, interval=self.kline_interval, limit=self.kline_limit)
            if not klines or len(klines) < self.kline_limit:
                logger.warning(f"[{self.strategy_id}] Недостатньо даних K-ліній для аналізу ({len(klines)} з {self.kline_limit} потрібних).")
                return None

            df = pd.DataFrame(klines, columns=['open_time', 'open', 'high', 'low', 'close', 'volume', 'close_time',
                                               'quote_asset_volume', 'number_of_trades', 'taker_buy_base_asset_volume',
                                               'taker_buy_quote_asset_volume', 'ignore'])

        # Конвертуємо колонки у числовий тип
        for col in ['open', 'high', 'low', 'close', 'volume']:
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
            df.ta.sma(close=df['volume'], length=self.volume_ma_period, append=True, col_names=(f'VOLUME_MA_{self.volume_ma_period}',))
        if f'ATR_{self.atr_period}' not in df.columns:
            df.ta.atr(length=self.atr_period, append=True, col_names=(f'ATR_{self.atr_period}',))

        # Видаляємо рядки з NaN після розрахунку індикаторів
        df.dropna(inplace=True)
        if len(df) < 2:
            return None # Потрібно мінімум 2 рядки для порівняння

        # Поточна (закрита) та попередня свічки
        current_candle = df.iloc[-1]
        prev_candle = df.iloc[-2]

        # --- Умови для Long ---
        is_golden_cross = prev_candle[f'EMA_{self.fast_ema_period}'] < prev_candle[f'EMA_{self.slow_ema_period}'] and \
                          current_candle[f'EMA_{self.fast_ema_period}'] > current_candle[f'EMA_{self.slow_ema_period}']

        is_upward_slope = current_candle[f'EMA_{self.fast_ema_period}'] > prev_candle[f'EMA_{self.fast_ema_period}'] and \
                          current_candle[f'EMA_{self.slow_ema_period}'] > prev_candle[f'EMA_{self.slow_ema_period}']

        is_momentum_strong = not self.use_rsi_filter or current_candle[f'RSI_{self.rsi_period}'] > 50
        is_volume_confirmed = not self.use_volume_filter or current_candle['volume'] >= current_candle[f'VOLUME_MA_{self.volume_ma_period}']

        if is_golden_cross and is_upward_slope and is_momentum_strong and is_volume_confirmed:
            logger.info(f"[{self.strategy_id}] Знайдено сигнал LONG для {self.symbol} по ціні {current_candle['close']:.4f}")
            return {
                'signal_type': 'Long',
                'entry_price': current_candle['close'],
                'atr': current_candle[f'ATR_{self.atr_period}'],
                'dataframe': df
            }

        # --- Умови для Short ---
        is_death_cross = prev_candle[f'EMA_{self.fast_ema_period}'] > prev_candle[f'EMA_{self.slow_ema_period}'] and \
                         current_candle[f'EMA_{self.fast_ema_period}'] < current_candle[f'EMA_{self.slow_ema_period}']

        is_downward_slope = current_candle[f'EMA_{self.fast_ema_period}'] < prev_candle[f'EMA_{self.fast_ema_period}'] and \
                            current_candle[f'EMA_{self.slow_ema_period}'] < prev_candle[f'EMA_{self.slow_ema_period}']

        is_momentum_weak = not self.use_rsi_filter or current_candle[f'RSI_{self.rsi_period}'] < 50

        if is_death_cross and is_downward_slope and is_momentum_weak and (not self.use_volume_filter or is_volume_confirmed):
            logger.info(f"[{self.strategy_id}] Знайдено сигнал SHORT для {self.symbol} по ціні {current_candle['close']:.4f}")
            return {
                'signal_type': 'Short',
                'entry_price': current_candle['close'],
                'atr': current_candle[f'ATR_{self.atr_period}'],
                'dataframe': df
            }

        return None

    def calculate_sl_tp(self, entry_price: float, signal_type: str, order_book_manager: 'OrderBookManager', tick_size: float, **kwargs) -> dict | None:
        """
        Розраховує Stop-Loss та Take-Profit.
        Підтримує два методи для TP: 'rr_ratio' (співвідношення ризик/прибуток) та 'local_extremum' (наступний локальний максимум/мінімум).
        """
        atr = kwargs.get('atr')
        dataframe = kwargs.get('dataframe')

        if not atr:
            logger.error(f"[{self.strategy_id}] Неможливо розрахувати SL/TP: не передано значення ATR.")
            return None

        # Розрахунок Stop Loss
        if signal_type == 'Long':
            stop_loss_price = entry_price - (self.sl_atr_multiplier * atr)
        elif signal_type == 'Short':
            stop_loss_price = entry_price + (self.sl_atr_multiplier * atr)
        else:
            return None

        # Розрахунок Take Profit
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

        # Якщо TP не було розраховано за екстремумом, використовуємо R:R
        if take_profit_price is None:
            logger.debug(f"[{self.strategy_id}] Не вдалося знайти локальний екстремум, TP розраховується за R:R = {self.rr_ratio}")
            if signal_type == 'Long':
                risk_amount = entry_price - stop_loss_price
                take_profit_price = entry_price + (self.rr_ratio * risk_amount)
            else: # Short
                risk_amount = stop_loss_price - entry_price
                take_profit_price = entry_price - (self.rr_ratio * risk_amount)

        # Вирівнюємо до tick_size
        stop_loss_price = round(stop_loss_price / tick_size) * tick_size
        take_profit_price = round(take_profit_price / tick_size) * tick_size
        
        logger.debug(f"[{self.strategy_id}] Розраховано для {signal_type} @ {entry_price:.4f}: SL={stop_loss_price:.4f}, TP={take_profit_price:.4f}")

        return {'stop_loss': stop_loss_price, 'take_profit': take_profit_price}


