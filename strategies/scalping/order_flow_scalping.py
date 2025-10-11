import pandas as pd
import numpy as np
from strategies.base_strategy import BaseStrategy
from loguru import logger
import yaml

class OrderFlowScalpingStrategy(BaseStrategy):
    """
    A scalping strategy based on Order Flow concepts, primarily focusing on
    Volume Delta and Exhaustion points.
    """

    def __init__(self, strategy_id: str, symbol: str, interval: str, parameters: dict):
        super().__init__(strategy_id, symbol, interval, parameters)

        params_file = parameters.get("params_file", "configs/orderflow_params.yaml")
        params_key = parameters.get("params_key", "default")
        
        all_params = self._load_params_from_file(params_file)
        
        default_params = all_params.get("default", {})
        strategy_params = all_params.get(params_key, {})

        # Merge default and strategy-specific params
        final_params = {**default_params, **strategy_params}

        # General parameters
        self.volume_threshold_multiplier = final_params.get("volume_threshold_multiplier", 2.5)
        self.body_size_threshold_pct = final_params.get("body_size_threshold_pct", 0.2)
        self.climax_bar_lookback = final_params.get("climax_bar_lookback", 10)
        self.cvd_sma_period = final_params.get("cvd_sma_period", 5)
        self.tp_atr_multiplier = final_params.get("tp_atr_multiplier", 1.5) # Make sure this is loaded
        self.sl_atr_multiplier = final_params.get("sl_atr_multiplier", 1.0) # New parameter
        
        # Load setups from parameters
        self.setups = final_params.get("setups", {})

        logger.info(f"[{self.strategy_id}] Initialized with setups from {params_file} using key '{params_key}'")

    def _load_params_from_file(self, path: str) -> dict:
        try:
            with open(path, 'r') as f:
                return yaml.safe_load(f)
        except (FileNotFoundError, yaml.YAMLError) as e:
            logger.error(f"[{self.strategy_id}] Error loading params file from {path}: {e}. Using empty params.")
            return {}

    def check_signal(self, df: pd.DataFrame, current_cvd: float = 0.0) -> dict | None:
        """
        Checks for trading signals based on the enabled setups.
        """
        if len(df) < max(self.climax_bar_lookback + 2, self.cvd_sma_period):
            return None

        # Exhaustion Short
        exhaustion_short_setup = self.setups.get("exhaustion_short", {})
        if exhaustion_short_setup.get("enabled", False):
            signal = self._check_exhaustion_signal(df, current_cvd, "short", exhaustion_short_setup.get("conditions", {}))
            if signal:
                return signal

        # Exhaustion Long
        exhaustion_long_setup = self.setups.get("exhaustion_long", {})
        if exhaustion_long_setup.get("enabled", False):
            signal = self._check_exhaustion_signal(df, current_cvd, "long", exhaustion_long_setup.get("conditions", {}))
            if signal:
                return signal

        # Absorption Short
        absorption_short_setup = self.setups.get("absorption_short", {})
        if absorption_short_setup.get("enabled", False):
            signal = self._check_absorption_signal(df, current_cvd, "short", absorption_short_setup.get("conditions", {}))
            if signal:
                return signal

        # Absorption Long
        absorption_long_setup = self.setups.get("absorption_long", {})
        if absorption_long_setup.get("enabled", False):
            signal = self._check_absorption_signal(df, current_cvd, "long", absorption_long_setup.get("conditions", {}))
            if signal:
                return signal

        return None

    def _check_exhaustion_signal(self, df: pd.DataFrame, current_cvd: float, signal_type: str, conditions: dict) -> dict | None:
        """Checks for an exhaustion signal."""
        latest_candle = df.iloc[-1]
        previous_candle = df.iloc[-2]
        lookback_df = df.iloc[-self.climax_bar_lookback-1:-1]

        # --- Conditions ---
        avg_volume = lookback_df['volume'].mean()
        is_high_volume = latest_candle['volume'] > avg_volume * self.volume_threshold_multiplier
        candle_range = latest_candle['high'] - latest_candle['low']
        candle_body = abs(latest_candle['close'] - latest_candle['open'])
        is_small_body = candle_body < candle_range * self.body_size_threshold_pct if candle_range > 0 else True
        is_uptrend = previous_candle['close'] > previous_candle['open']
        is_downtrend = previous_candle['close'] < previous_candle['open']
        is_new_high = latest_candle['high'] >= lookback_df['high'].max()
        is_new_low = latest_candle['low'] <= lookback_df['low'].min()
        
        cvd_sma = df['cvd'].rolling(window=self.cvd_sma_period).mean().iloc[-1]
        cvd_bearish_divergence = current_cvd < cvd_sma
        cvd_bullish_divergence = current_cvd > cvd_sma

        if signal_type == "short" and is_uptrend:
            all_conditions_met = all([
                not conditions.get("is_high_volume", True) or is_high_volume,
                not conditions.get("is_small_body", True) or is_small_body,
                not conditions.get("is_new_high", True) or is_new_high,
                not conditions.get("cvd_bearish_divergence", True) or cvd_bearish_divergence,
            ])
            if all_conditions_met:
                logger.debug(f"[{self.symbol}@{latest_candle.name}] Potential Short (Exhaustion): HighVol={is_high_volume}, SmallBody={is_small_body}, NewHigh={is_new_high}, CVDBearish={cvd_bearish_divergence}")
                return {"signal_type": "Short", "entry_price": latest_candle['close'], "candle": latest_candle}

        elif signal_type == "long" and is_downtrend:
            all_conditions_met = all([
                not conditions.get("is_high_volume", True) or is_high_volume,
                not conditions.get("is_small_body", True) or is_small_body,
                not conditions.get("is_new_low", True) or is_new_low,
                not conditions.get("cvd_bullish_divergence", True) or cvd_bullish_divergence,
            ])
            if all_conditions_met:
                logger.debug(f"[{self.symbol}@{latest_candle.name}] Potential Long (Exhaustion): HighVol={is_high_volume}, SmallBody={is_small_body}, NewLow={is_new_low}, CVDBullish={cvd_bullish_divergence}")
                return {"signal_type": "Long", "entry_price": latest_candle['close'], "candle": latest_candle}
        
        return None

    def _check_absorption_signal(self, df: pd.DataFrame, current_cvd: float, signal_type: str, conditions: dict) -> dict | None:
        """Checks for an absorption signal."""
        latest_candle = df.iloc[-1]
        lookback_df = df.iloc[-self.climax_bar_lookback-1:-1]

        # --- Conditions ---
        avg_volume = lookback_df['volume'].mean()
        is_high_volume = latest_candle['volume'] > avg_volume * self.volume_threshold_multiplier
        candle_range = latest_candle['high'] - latest_candle['low']
        upper_wick = latest_candle['high'] - max(latest_candle['open'], latest_candle['close'])
        lower_wick = min(latest_candle['open'], latest_candle['close']) - latest_candle['low']
        is_rejection_from_high = upper_wick > candle_range * 0.6 if candle_range > 0 else False
        is_rejection_from_low = lower_wick > candle_range * 0.6 if candle_range > 0 else False
        is_at_high = latest_candle['high'] >= lookback_df['high'].max()
        is_at_low = latest_candle['low'] <= lookback_df['low'].min()
        
        cvd_sma = df['cvd'].rolling(window=self.cvd_sma_period).mean().iloc[-1]
        cvd_bearish_divergence = current_cvd < cvd_sma
        cvd_bullish_divergence = current_cvd > cvd_sma

        if signal_type == "short":
            all_conditions_met = all([
                not conditions.get("is_at_high", True) or is_at_high,
                not conditions.get("is_high_volume", True) or is_high_volume,
                not conditions.get("is_rejection_from_high", True) or is_rejection_from_high,
                not conditions.get("cvd_bearish_divergence", True) or cvd_bearish_divergence,
            ])
            if all_conditions_met:
                logger.debug(f"[{self.symbol}@{latest_candle.name}] Potential Short (Absorption): HighVol={is_high_volume}, WickRejection={is_rejection_from_high}, AtHigh={is_at_high}, CVDBearish={cvd_bearish_divergence}")
                logger.success(f"[{self.symbol}] Confirmed SHORT signal (Absorption) at {latest_candle['close']}")
                return {"signal_type": "Short", "entry_price": latest_candle['close'], "candle": latest_candle}

        elif signal_type == "long":
            all_conditions_met = all([
                not conditions.get("is_at_low", True) or is_at_low,
                not conditions.get("is_high_volume", True) or is_high_volume,
                not conditions.get("is_rejection_from_low", True) or is_rejection_from_low,
                not conditions.get("cvd_bullish_divergence", True) or cvd_bullish_divergence,
            ])
            if all_conditions_met:
                logger.debug(f"[{self.symbol}@{latest_candle.name}] Potential Long (Absorption): HighVol={is_high_volume}, WickRejection={is_rejection_from_low}, AtLow={is_at_low}, CVDBullish={cvd_bullish_divergence}")
                logger.success(f"[{self.symbol}] Confirmed LONG signal (Absorption) at {latest_candle['close']}")
                return {"signal_type": "Long", "entry_price": latest_candle['close'], "candle": latest_candle}

        return None

    def calculate_sl_tp(self, entry_price: float, signal_type: str, df: pd.DataFrame, fee_pct: float) -> dict | None:
        """
        Calculates Stop-Loss and Take-Profit based on the signal candle, accounting for fees.
        Returns None if the trade is not profitable after fees.
        """
        atr = df['high'] - df['low'] # Simplified ATR for volatility
        avg_atr = atr.iloc[-10:].mean()

        # Fees for entry and exit
        total_fees = entry_price * 2 * fee_pct

        # Check for profitability
        if (avg_atr * self.tp_atr_multiplier) <= total_fees:
            logger.warning(f"[{self.strategy_id}] Trade is not profitable after fees. Profit vs Fees: {(avg_atr * self.tp_atr_multiplier):.5f} vs {total_fees:.5f}. Skipping trade.")
            return None

        if signal_type == "Short":
            stop_loss = entry_price + (avg_atr * self.sl_atr_multiplier)
            take_profit = entry_price - (avg_atr * self.tp_atr_multiplier)

        elif signal_type == "Long":
            stop_loss = entry_price - (avg_atr * self.sl_atr_multiplier)
            take_profit = entry_price + (avg_atr * self.tp_atr_multiplier)

        else:
            return None

        return {
            "stop_loss": stop_loss,
            "take_profit": take_profit
        }
