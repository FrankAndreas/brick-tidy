# Build stage: install the dependencies into a virtual environment.
FROM python:3.14-slim AS build
RUN python -m venv /opt/venv
COPY backend/requirements.txt /tmp/requirements.txt
RUN /opt/venv/bin/pip install --no-cache-dir -r /tmp/requirements.txt \
    && /opt/venv/bin/pip uninstall -y pip

# Runtime stage: the finished environment plus the app, no install step.
FROM python:3.14-slim
COPY --from=build /opt/venv /opt/venv

WORKDIR /app
COPY backend/app backend/app
COPY frontend frontend
COPY scripts scripts
# Precompiled, since the app user can't write bytecode next to the code
RUN /opt/venv/bin/python -m compileall -q backend/app scripts

# Runs as an unprivileged user; compose files may choose another one (PUID/PGID)
# as long as it owns the data folder.
RUN groupadd -g 1000 app && useradd -u 1000 -g app -M -d /data app \
    && mkdir /data && chown app:app /data

ENV PATH=/opt/venv/bin:$PATH \
    DATA_DIR=/data \
    FRONTEND_DIR=/app/frontend \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 8080
USER app
WORKDIR /app/backend
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8080/api/status', timeout=5)"]
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080"]
