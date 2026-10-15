# Custodian: one image serving the API and the web app from the same origin.
#   docker build -t custodian .
#   docker run -p 8000:8000 -v custodian-data:/data --env-file backend/.env custodian

# ---- 1. Export the Expo app for the web --------------------------------------------
FROM node:22-bookworm-slim AS web
WORKDIR /web
COPY mobile/package.json mobile/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY mobile/ ./
# No EXPO_PUBLIC_API_BASE: a production web build calls the API on its own origin.
RUN npx expo export --platform web --output-dir dist

# ---- 2. Python runtime ---------------------------------------------------------------
FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY backend/requirements.txt ./
RUN pip install -r requirements.txt

COPY backend/app ./app
COPY backend/skills ./skills
COPY backend/schema.sql ./
COPY --from=web /web/dist ./web

RUN useradd --system --uid 10001 --home-dir /nonexistent --shell /usr/sbin/nologin custodian \
    && mkdir -p /data && chown custodian:custodian /data
USER custodian

ENV CUSTODIAN_ENV=production \
    DATA_DIR=/data \
    WEB_DIST_DIR=/app/web \
    CORS_ORIGINS=

VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"

# One worker: the scheduler runs inside this process. --proxy-headers lets the login rate
# limiter see real client IPs behind the reverse proxy, but only for requests coming from
# FORWARDED_ALLOW_IPS (read by uvicorn; docker-compose.yml sets it to Caddy's address).
# Trusting every address would let anyone reaching port 8000 directly forge X-Forwarded-For
# and get a fresh set of sign-in attempts each time.
ENV FORWARDED_ALLOW_IPS=127.0.0.1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", \
     "--proxy-headers", "--no-server-header"]
