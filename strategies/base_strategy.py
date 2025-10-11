from abc import ABC, abstractmethod
import pandas as pd

class BaseStrategy(ABC):
    """Abstract base class for all trading strategies."""

    def __init__(self, strategy_id: str, symbol: str, interval: str, parameters: dict):
        self.strategy_id = strategy_id
        self.symbol = symbol
        self.interval = interval
        self.parameters = parameters

    @abstractmethod
    def check_signal(self, df: pd.DataFrame) -> dict | None:
        """Checks for a trading signal (Long/Short) based on the provided DataFrame.

        Returns a dictionary with signal details or None if no signal.
        """
        pass

    @abstractmethod
    def calculate_sl_tp(self, entry_price: float, signal_type: str, df: pd.DataFrame, fee_pct: float) -> dict:
        """Calculates Stop-Loss and Take-Profit levels for a given entry price and signal type.

        Returns a dictionary with 'stop_loss' and 'take_profit' values.
        """
        pass
