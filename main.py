import asyncio
from loguru import logger
import sys
from core.bot_orchestrator import BotOrchestrator

# --- Конфігурація логера ---
# Видаляємо стандартний обробник, щоб уникнути дублювання логів.
logger.remove() 

# Додаємо новий обробник для виводу в консоль (stderr).
# Рівень DEBUG означає, що всі повідомлення від DEBUG і вище (INFO, WARNING, ERROR, CRITICAL) будуть показані.
logger.add(
    sys.stderr, 
    level="DEBUG",
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>"
) 

# Додаємо обробник для запису логів у файл.
logger.add(
    "logs/trader_{time:YYYY-MM-DD}.log",      # Назва файлу з автоматичною датою.
    level="DEBUG",                 # Рівень логування для файлу.
    rotation="10 MB",              # Ротація файлу (новий файл після досягнення 10 MB).
    compression="zip",             # Стиснення старих лог-файлів у zip-архіви.
    serialize=True,                  # Запис логів у форматі JSON для зручного машинного аналізу.
    enqueue=True,                    # Робить логування асинхронно безпечним.
    backtrace=True,                  # Показує повний стек викликів при помилках.
    diagnose=True                    # Додає діагностичну інформацію при помилках.
)

logger.info("Логер успішно налаштовано. Запуск додатку...")

async def main():
    """
    Головна асинхронна функція, що ініціалізує та запускає бота.
    """
    try:
        # Створюємо екземпляр головного класу-оркестратора.
        orchestrator = BotOrchestrator()
        # Запускаємо основну логіку бота. Цей метод буде працювати безкінечно.
        await orchestrator.start()
    except Exception as e:
        # Логуємо будь-які критичні помилки, що можуть виникнути на етапі ініціалізації.
        logger.critical(f"Помилка під час ініціалізації або запуску BotOrchestrator: {e}", exc_info=True)

if __name__ == "__main__":
    try:
        # Запускаємо головну асинхронну функцію за допомогою asyncio.run().
        asyncio.run(main())
    except KeyboardInterrupt:
        # Обробка елегантного завершення роботи програми при натисканні Ctrl+C.
        logger.info("Додаток зупинено користувачем (Ctrl+C).")
    except Exception as e:
        # Логування будь-яких інших непередбачуваних помилок на верхньому рівні.
        logger.critical(f"Виникла непередбачувана помилка на глобальному рівні: {e}", exc_info=True)