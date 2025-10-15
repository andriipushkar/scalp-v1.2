import argparse
import asyncio
import pandas as pd
from datetime import datetime

from core.binance_client import BinanceClient

async def get_trades_history(symbol: str | None, start_date: str | None, end_date: str | None, output_file: str | None):
    """
    Асинхронно отримує, відображає та зберігає у файл історію угод.

    Args:
        symbol (str | None): Торговий символ (напр., "BTCUSDT") або None для всіх символів.
        start_date (str | None): Дата початку у форматі 'YYYY-MM-DD'.
        end_date (str | None): Дата кінця у форматі 'YYYY-MM-DD'.
        output_file (str | None): Шлях до файлу для збереження результату у форматі CSV.
    """
    start_timestamp = int(datetime.strptime(start_date, "%Y-%m-%d").timestamp() * 1000) if start_date else None
    end_timestamp = int(datetime.strptime(end_date, "%Y-%m-%d").timestamp() * 1000) if end_date else None

    async with BinanceClient() as client:
        try:
            symbols_to_check = []
            if symbol:
                symbols_to_check.append(symbol)
                print(f"Отримання історії для символу: {symbol}")
            else:
                print("Параметр --symbol не вказано. Отримання списку всіх символів з акаунту...")
                symbols_to_check = await client.get_all_account_symbols()
                print(f"Знайдено {len(symbols_to_check)} символів. Отримання угод для кожного...")

            all_trades = []
            for s in symbols_to_check:
                try:
                    print(f"- Завантаження для {s}...")
                    trades = await client.get_account_trades(
                        symbol=s,
                        start_time=start_timestamp,
                        end_time=end_timestamp
                    )
                    all_trades.extend(trades)
                    await asyncio.sleep(0.1) # Невеликий таймаут, щоб не перевищити ліміти API
                except Exception as e:
                    print(f"Помилка отримання угод для {s}: {e}")

            if not all_trades:
                print(f"Не знайдено угод за вказаний період.")
                return

            # Створюємо DataFrame для зручного відображення
            df = pd.DataFrame(all_trades)
            df.sort_values(by='time', inplace=True) # Сортуємо всі угоди по часу

            # Конвертуємо час з мілісекунд у читабельний формат
            df['time'] = pd.to_datetime(df['time'], unit='ms')

            # Вибираємо та перейменовуємо колонки для звіту
            report_df = df[['time', 'id', 'orderId', 'symbol', 'side', 'positionSide', 'maker', 'price', 'qty', 'quoteQty', 'realizedPnl', 'commission', 'commissionAsset']]
            report_df = report_df.rename(columns={
                'time': 'Час',
                'id': 'ID Угоди',
                'orderId': 'ID Ордеру',
                'symbol': 'Символ',
                'side': 'Сторона',
                'positionSide': 'Позиція',
                'maker': 'Мейкер',
                'price': 'Ціна',
                'qty': 'Кількість',
                'quoteQty': "Об'єм (USDT)",
                'realizedPnl': 'PNL',
                'commission': 'Комісія',
                'commissionAsset': 'Актив комісії'
            })
            
            # Встановлюємо 'Час' як індекс для кращого вигляду
            report_df.set_index('Час', inplace=True)

            print(f"\nІсторія угод:\n")
            # Налаштування для виводу всіх колонок
            pd.set_option('display.max_columns', None)
            pd.set_option('display.width', 200)
            print(report_df)

            # Розрахунок та вивід сумарного PNL
            total_pnl = df['realizedPnl'].astype(float).sum()
            print(f"\nСумарний PNL за період: {total_pnl:.4f} USDT")

            # Збереження у файл, якщо вказано
            if output_file:
                try:
                    report_df.to_csv(output_file)
                    print(f"\nЗвіт успішно збережено у файл: {output_file}")
                except Exception as e:
                    print(f"\nПомилка збереження файлу: {e}")

        except Exception as e:
            print(f"Сталася помилка: {e}")

def main():
    """
    Головна функція для запуску скрипта з командного рядка.
    """
    parser = argparse.ArgumentParser(description="Отримати історію позицій (угод) з Binance Futures.")
    parser.add_argument("--symbol", type=str, help="Символ для отримання історії (напр. BTCUSDT). Якщо не вказано, буде отримано історію для всіх символів.")
    parser.add_argument("--start", type=str, help="Дата початку у форматі YYYY-MM-DD.")
    parser.add_argument("--end", type=str, help="Дата кінця у форматі YYYY-MM-DD.")
    parser.add_argument("--output", type=str, help="Назва CSV файлу для збереження звіту (напр. history.csv).")

    args = parser.parse_args()

    # Запуск асинхронної функції
    asyncio.run(get_trades_history(args.symbol, args.start, args.end, args.output))


if __name__ == "__main__":
    main()
