import asyncio
import os
from dotenv import load_dotenv
from binance import AsyncClient, BinanceSocketManager

async def main():
    """
    Цей скрипт підключається до Binance User Data Stream для ф'ючерсів
    і виводить на екран будь-які отримані повідомлення. 
    Це допомагає перевірити, чи працює API-ключ та з'єднання.
    """
    print("--- Тест User Data Stream для Binance Futures ---")
    print("Завантаження ключів API з .env файлу...")
    load_dotenv()
    api_key = os.getenv("BINANCE_API_KEY")
    api_secret = os.getenv("BINANCE_API_SECRET")

    if not api_key or not api_secret:
        print("\nПОМИЛКА: Переконайтеся, що BINANCE_API_KEY та BINANCE_API_SECRET є у вашому .env файлі.")
        return

    print("Ключі завантажено. Створення клієнта...")
    client = await AsyncClient.create(api_key, api_secret)
    bsm = BinanceSocketManager(client)

    try:
        print("\n--- ПІДКЛЮЧЕННЯ ДО WEBSOCKET ---")
        print("Скрипт чекає на повідомлення від Binance...")
        print("ЩО РОБИТИ ДАЛІ: ")
        print("1. НЕ ЗУПИНЯЙТЕ цей скрипт.")
        print("2. Зайдіть на сайт або в додаток Binance.")
        print("3. Виконайте будь-яку дію на ф'ючерсному рахунку: виставте, а потім скасуйте лімітний ордер.")
        print("4. Подивіться, чи з'явиться повідомлення в терміналі нижче.")
        print("--------------------------------------------------\n")

        # Використовуємо правильний метод futures_user_socket(), який не потребує listen_key
        async with bsm.futures_user_socket() as socket:
            while True:
                try:
                    msg = await socket.recv()
                    print(f"[!!!] ОТРИМАНО ПОВІДОМЛЕННЯ: {msg}")
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    print(f"Помилка під час отримання повідомлення: {e}")
                    break

    except Exception as e:
        print(f"\nВиникла критична помилка під час налаштування: {e}")
    finally:
        await client.close_connection()
        print("\n--- З'єднання закрито. ---")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nСкрипт зупинено користувачем.")