# Використовуємо офіційний образ Python
FROM python:3.12-slim

# Встановлюємо робочу директорію в контейнері
WORKDIR /app

# Копіюємо файл залежностей та встановлюємо їх
# Це робиться окремим кроком для кешування Docker
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копіюємо решту коду додатку
COPY . .

# Команда для запуску бота при старті контейнера
CMD ["python", "main.py"]
