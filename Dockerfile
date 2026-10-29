# mednik — single container: FastAPI serves the API and the pre-built dashboard on one port.
# Used by Render (render.yaml); runs anywhere Docker does:
#   docker build -t mednik . && docker run -p 10000:10000 -e MEDNIK_APP_PASSWORD=… mednik

# ---- dashboard (static export) ------------------------------------------------
FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
ENV MEDNIK_STATIC_EXPORT=1 NEXT_TELEMETRY_DISABLED=1
RUN npx next build

# ---- API ------------------------------------------------------------------------
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONUTF8=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app/backend
COPY backend/requirements.txt ./
# runtime dependencies only (the file's "# dev / test" block is skipped)
RUN sed '/^# dev/,$d' requirements.txt > /tmp/runtime.txt && pip install -r /tmp/runtime.txt
COPY backend/ ./
COPY --from=web /web/out /app/static
RUN useradd --create-home --uid 1000 mednik && mkdir -p uploads && chown -R mednik /app/backend
USER mednik
ENV MEDNIK_STATIC_DIR=/app/static MEDNIK_CORS_ORIGINS="" PORT=10000
EXPOSE 10000
CMD ["sh", "-c", "exec uvicorn mednik.api.main:app --host 0.0.0.0 --port ${PORT:-10000} --proxy-headers --forwarded-allow-ips='*' --timeout-keep-alive 75"]
