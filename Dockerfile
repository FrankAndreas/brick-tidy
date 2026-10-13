# Build stage: install the dependencies into a virtual environment.
FROM python:3.12-slim AS build
RUN python -m venv /opt/venv
COPY backend/requirements.txt /tmp/requirements.txt
RUN /opt/venv/bin/pip install --no-cache-dir -r /tmp/requirements.txt \
    && /opt/venv/bin/pip uninstall -y pip

# Runtime stage: the finished environment plus the app, no install step.
FROM python:3.12-slim
COPY --from=build /opt/venv /opt/venv

WORKDIR /app
COPY backend/app backend/app
COPY frontend frontend
COPY scripts scripts

ENV PATH=/opt/venv/bin:$PATH \
    DATA_DIR=/data \
    FRONTEND_DIR=/app/frontend \
    PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 8080
WORKDIR /app/backend
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080"]
