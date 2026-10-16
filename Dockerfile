# Multi-stage build: frontend + backend (Railway single image)
# Stage 1: Build frontend
FROM node:22-slim AS frontend-build

RUN corepack enable && corepack prepare pnpm@9.15.0 --activate

WORKDIR /frontend
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/ .
# Публичный адрес: http://<хост>/soilplanner/  (nginx срезает префикс до приложения)
ENV VITE_BASE_PATH=/soilplanner/
RUN pnpm build

# Stage 2: Python backend + serve static
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential wget \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY backend/ .

# Frontend dist → /app/static/ (main.py reads from here)
COPY --from=frontend-build /frontend/dist ./static/

# Railway sets PORT automatically
ENV PORT=8000

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD sh -c "wget --no-verbose --tries=1 --spider http://localhost:$$PORT/api/health || exit 1"

CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT}
