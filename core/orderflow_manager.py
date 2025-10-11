import asyncio
from loguru import logger

class OrderflowManager:
    """Calculates Cumulative Volume Delta (CVD) from aggregate trade data."""

    def __init__(self, symbol: str):
        self.symbol = symbol
        self.cumulative_volume_delta = 0.0
        self.trade_count = 0
        logger.info(f"OrderflowManager initialized for {symbol}.")

    async def process_aggtrade_message(self, trade: dict):
        """Processes a single aggregate trade message from the websocket."""
        if isinstance(trade, dict) and 'q' in trade and 'm' in trade:
            self.trade_count += 1
            quantity = float(trade['q'])
            is_buyer_maker = trade['m']

            if is_buyer_maker:
                self.cumulative_volume_delta -= quantity
            else:
                self.cumulative_volume_delta += quantity
            
            if self.trade_count % 100 == 0:
                logger.debug(f"[{self.symbol}] CVD: {self.cumulative_volume_delta:.2f}")

# Example of how to run this manager independently (for testing)
async def main():
    # This is for testing purposes only
    pass

if __name__ == "__main__":
    pass
