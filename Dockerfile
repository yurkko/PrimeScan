FROM python:3.12-slim

WORKDIR /app
COPY . .

# Ставимо системні пакунки для збірки залежностей (якщо потрібно)
RUN apt-get update && \
    apt-get install -y --no-install-recommends build-essential && \
    rm -rf /var/lib/apt/lists/*

# Оновлюємо pip та ставимо requirements глобально
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Запуск
CMD ["python", "main.py"]
