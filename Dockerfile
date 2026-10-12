FROM python:3.12-slim

WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend/app backend/app
COPY frontend frontend
COPY scripts scripts

ENV DATA_DIR=/data \
    FRONTEND_DIR=/app/frontend \
    PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 8080
WORKDIR /app/backend
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080"]
