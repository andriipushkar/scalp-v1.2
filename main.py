import asyncio
from loguru import logger
import sys
from core.bot_orchestrator import BotOrchestrator

# --- Logger configuration ---
logger.remove()
logger.add(sys.stderr, level="DEBUG") # Log to console
logger.add(
    "logs/trader_{time}.log",
    level="DEBUG",
    rotation="10 MB",
    compression="zip",
    serialize=True # To write logs in JSON format
)

logger.info("Logger configured. Starting application...")

async def main():
    orchestrator = BotOrchestrator()
    await orchestrator.start()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Application stopped by user.")
    except Exception as e:
        logger.error(f"An unexpected error occurred: {e}")