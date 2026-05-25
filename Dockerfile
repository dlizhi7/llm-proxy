FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai \
    PORT=8080

# Install runtime dependencies first for better layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY main.py .

# Create a fallback config.py — overridden by docker-compose volume mount at runtime.
RUN echo "# Fallback config — overridden by volume mount" > /app/config.py; \
    echo "DEEPSEEK_API_KEYS = []" >> /app/config.py; \
    echo "PROXY_API_KEY = \"\"" >> /app/config.py; \
    echo "PROXY_MODEL_ALIASES = []" >> /app/config.py

EXPOSE 8080

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
