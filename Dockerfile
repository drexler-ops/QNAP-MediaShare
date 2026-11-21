# ---- 1. build the React frontend -------------------------------------------------------
FROM node:22-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---- 2. runtime: FastAPI + worker + exiftool + ffmpeg -----------------------------------
FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    FRONTEND_DIST=/app/frontend_dist
RUN apt-get update \
 && apt-get install -y --no-install-recommends libimage-exiftool-perl ffmpeg tini \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app/backend
COPY backend/requirements.txt .
RUN pip install -r requirements.txt
COPY backend/ .
COPY --from=web /web/dist /app/frontend_dist
EXPOSE 8080
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers"]

# ---- 3. test image: `docker build --target test .` runs the full test suite --------------
FROM runtime AS test
COPY backend/requirements-dev.txt .
RUN pip install -r requirements-dev.txt
RUN python -m pytest -q
