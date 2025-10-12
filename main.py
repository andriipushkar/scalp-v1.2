import asyncio
from loguru import logger
import sys
from core.bot_orchestrator import BotOrchestrator

# --- Конфігурація логера ---
logger.remove() # Видаляємо стандартний обробник

# Додаємо обробник для виводу в консоль з рівнем DEBUG
logger.add(sys.stderr, level="DEBUG") 

# Додаємо обробник для запису логів у файл
logger.add(
    "logs/trader_{time}.log",      # Назва файлу з автоматичною датою та часом
    level="DEBUG",                 # Рівень логування
    rotation="10 MB",              # Ротація файлу при досягненні 10 MB
    compression="zip",             # Стиснення старих лог-файлів
    serialize=True                 # Запис логів у форматі JSON для зручного парсингу
)

logger.info("Логер налаштовано. Запуск додатку...")

async def main():
    """Головна асинхронна функція, що ініціалізує та запускає бота."""
    orchestrator = BotOrchestrator()
    await orchestrator.start()

if __name__ == "__main__":
    try:
        # Запускаємо головну асинхронну функцію
        asyncio.run(main())
    except KeyboardInterrupt:
        # Обробка зупинки бота користувачем (Ctrl+C)
        logger.info("Додаток зупинено користувачем.")
    except Exception as e:
        # Обробка будь-яких інших непередбачуваних помилок
        logger.critical(f"Виникла непередбачувана помилка: {e}")
