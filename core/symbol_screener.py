import asyncio
from loguru import logger
from core.binance_client import BinanceClient

class SymbolScreener:
    """
    Відповідає за вибір найкращих символів для торгівлі на основі ринкових даних.
    """

    def __init__(self, binance_client: BinanceClient):
        self.binance_client = binance_client

    async def get_top_symbols_by_volume(self, n: int = 20) -> list[str]:
        """
        Отримує топ N символів за 24-годинним обсягом торгів.

        :param n: Кількість символів для повернення.
        :return: Список назв символів.
        """
        logger.info(f"Запуск скринера: пошук топ-{n} символів за обсягом торгів...")
        try:
            all_tickers = await self.binance_client.get_futures_ticker()

            # Фільтруємо тільки безстрокові контракти до USDT
            usdt_perpetual_tickers = [
                ticker for ticker in all_tickers
                if ticker['symbol'].endswith('USDT') and not ticker['symbol'].endswith('_PERP') # Workaround for some symbols
            ]

            # Сортуємо за обсягом в USDT (quoteVolume)
            sorted_tickers = sorted(
                usdt_perpetual_tickers,
                key=lambda x: float(x['quoteVolume']),
                reverse=True
            )

            top_symbols = [ticker['symbol'] for ticker in sorted_tickers[:n]]
            logger.success(f"Скринер завершив роботу. Топ-{len(top_symbols)} символів: {top_symbols}")
            return top_symbols

        except Exception as e:
            logger.error(f"Помилка в роботі скринера символів: {e}")
            return []
