FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ALLOW_REMOTE=1 \
    COOKIE_SECURE=1 \
    BROWSER_HEADLESS=1 \
    BROWSER_CHANNEL="" \
    RUNTIME_DATA_DIR=/app/runtime

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && python -m playwright install --with-deps chromium

COPY backend ./backend
COPY frontend ./frontend
COPY data/showcase.json ./data/showcase.json

RUN mkdir -p /app/runtime

EXPOSE 8765
VOLUME ["/app/runtime"]
CMD ["sh", "-c", "python -m uvicorn backend.app:app --host 0.0.0.0 --port ${PORT:-8765} --proxy-headers --forwarded-allow-ips '*' "]
