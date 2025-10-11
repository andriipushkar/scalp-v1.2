class RiskManager:
    """Manages risk calculations for trading positions."""

    @staticmethod
    def calculate_position_size(entry_price: float, take_profit_pct: float) -> float:
        """Calculates the take profit price for a given entry price and percentage.

        For MVP, this is a simplified calculation.
        """
        # In MVP, we only calculate take profit price, not actual position size
        # as we are in dry-run mode.
        return entry_price * (1 + take_profit_pct / 100)
