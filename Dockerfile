FROM node:22-alpine AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    INPUT_DIR=/input \
    OUTPUT_DIR=/output \
    APPDATA_DIR=/appdata \
    PUID=99 \
    PGID=100

RUN apt-get update \
    && apt-get install -y --no-install-recommends gosu \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 1000 app \
    && useradd --uid 1000 --gid app --home-dir /app --no-create-home --shell /usr/sbin/nologin app

WORKDIR /app
COPY requirements.lock .
RUN pip install --no-cache-dir --require-hashes -r requirements.lock
COPY backlist ./backlist
COPY --from=ui /ui/dist ./backlist/static
COPY entrypoint.sh /entrypoint.sh
RUN chmod 755 /entrypoint.sh \
    && sed -i 's/\r$//' /entrypoint.sh

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health')"
ENTRYPOINT ["/entrypoint.sh"]
CMD ["python", "-m", "uvicorn", "backlist.web:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080"]
