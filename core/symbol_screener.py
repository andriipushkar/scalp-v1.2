import asyncio
from loguru import logger
from core.binance_client import BinanceClient

class SymbolScreener:
    """
    Відповідає за динамічний вибір найкращих торгових символів 
    на основі заданих ринкових критеріїв (напр., обсягу торгів).
    """

    def __init__(self, binance_client: BinanceClient):
        """
        Ініціалізує скринер.

        Args:
            binance_client (BinanceClient): Екземпляр клієнта Binance для доступу до API.
        """
        self.binance_client = binance_client

    async def get_top_symbols_by_volume(self, n: int = 20, min_volume: int = 100000000) -> list[str]:
        """
        Отримує топ N символів за 24-годинним обсягом торгів в USDT.

        Args:
            n (int): Максимальна кількість символів для повернення.
            min_volume (int): Мінімальний 24-годинний обсяг в USDT для включення символу.

        Returns:
            list[str]: Список назв символів, що відповідають критеріям.
        """
        logger.info(f"Запуск скринера: пошук топ-{n} символів з обсягом > ${min_volume:,}...")
        try:
            # Отримуємо 24-годинну статистику для всіх ф'ючерсних пар
            all_tickers = await self.binance_client.get_futures_ticker()

            # Фільтруємо тільки безстрокові контракти до USDT, що не є "сміттєвими"
            usdt_perpetual_tickers = [
                ticker for ticker in all_tickers
                if (
                    ticker['symbol'].endswith('USDT') and
                    not ticker['symbol'].startswith('DELE') and
                    ticker['symbol'].isascii() and  # Ігноруємо символи з не-ASCII символами (напр. китайські)
                    float(ticker['quoteVolume']) > min_volume
                )
            ]

            # Сортуємо відфільтровані символи за обсягом в USDT (quoteVolume) по спаданню
            sorted_tickers = sorted(
                usdt_perpetual_tickers,
                key=lambda x: float(x['quoteVolume']),
                reverse=True
            )

            # Повертаємо топ-N символів
            top_symbols = [ticker['symbol'] for ticker in sorted_tickers[:n]]
            logger.success(f"Скринер завершив роботу. Знайдено {len(top_symbols)} символів: {top_symbols}")
            return top_symbols

        except Exception as e:
            logger.error(f"Помилка в роботі скринера символів: {e}")
            return []