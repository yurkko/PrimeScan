FROM python:3.12-slim

WORKDIR /app
COPY . .

# Системні пакети для збірки «важких» залежностей
RUN apt-get update && \
    apt-get install -y --no-install-recommends build-essential libssl-dev libffi-dev python3-dev && \
    rm -rf /var/lib/apt/lists/*

# Оновлюємо pip
RUN python -m pip install --upgrade pip

# Інсталюємо залежності з детальним логом
RUN python -m pip install --no-cache-dir -r requirements.txt -v

# Запуск
CMD ["python", "research_bot.py"]
