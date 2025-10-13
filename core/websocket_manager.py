
import asyncio
from loguru import logger
from binance import BinanceSocketManager
from typing import Callable, Coroutine, Any

class WebSocketManager:
    """
    Керує мультиплексним WebSocket-з'єднанням з Binance,
    отримує повідомлення та маршрутизує їх до відповідних обробників.
    """
    def __init__(self, bsm: BinanceSocketManager, streams: list[str], 
                 user_data_handler: Callable[[dict], Coroutine[Any, Any, None]], 
                 depth_data_handler: Callable[[dict], Coroutine[Any, Any, None]]):
        """
        Ініціалізує менеджер вебсокетів.

        :param bsm: Екземпляр BinanceSocketManager.
        :param streams: Список потоків для підписки.
        :param user_data_handler: Асинхронна функція для обробки повідомлень користувача (ордери, баланс).
        :param depth_data_handler: Асинхронна функція для обробки ринкових даних (стакан).
        """
        self.bsm = bsm
        self.streams = streams
        self.user_data_handler = user_data_handler
        self.depth_data_handler = depth_data_handler

    async def start(self):
        """Запускає нескінченний цикл прослуховування та обробки повідомлень."""
        logger.info(f"Запуск мультиплексного вебсокету для потоків: {self.streams}")
        async with self.bsm.multiplex_socket(self.streams) as socket:
            while True:
                try:
                    msg = await socket.recv()

                    # Повідомлення про помилку (напр. розрив з'єднання)
                    if msg and 'e' in msg and 'm' in msg:
                        logger.error(f"Помилка вебсокету: {msg['m']}")
                        continue

                    # Маршрутизація повідомлення до відповідного обробника
                    if 'stream' in msg:
                        # Це ринкові дані (стакан, тіки тощо)
                        await self.depth_data_handler(msg)
                    elif 'e' in msg: 
                        # Це дані користувача (оновлення ордеру, балансу)
                        await self.user_data_handler(msg)

                except Exception as e:
                    logger.error(f"Критична помилка в слухачі вебсокетів: {e}. Перепідключення...")
                    # В реальній системі тут може бути логіка перепідключення
                    await asyncio.sleep(5)
