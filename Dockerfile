# One image for every way of running the dashboard: a Home Assistant App,
# plain `docker run` / Compose, or a Kubernetes pod. FastAPI serves the API and
# the built React UI from a single process on port 8099. All state lives in /data.

# --- build the UI -------------------------------------------------------------
FROM node:25-alpine AS ui
WORKDIR /src
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# --- runtime ------------------------------------------------------------------
FROM python:3.12-slim

# TZ: month boundaries and "today" follow UK time, BST included. Home
# Assistant passes its own time zone to Apps, which overrides this.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Europe/London \
    DATABASE_URL=sqlite:////data/finance.db \
    OPTIONS_FILE=/data/options.json \
    FRONTEND_DIST=/app/frontend/dist

WORKDIR /app/backend
# requirements.txt pins every package (generated from requirements.in), so a
# rebuild later installs exactly what was tested.
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir --no-deps --root-user-action=ignore --disable-pip-version-check \
    -r requirements.txt

COPY backend/app ./app
COPY --from=ui /src/dist /app/frontend/dist
RUN mkdir -p /data

VOLUME ["/data"]
EXPOSE 8099

# --no-proxy-headers: the client address must stay the real peer (in Home
# Assistant, the ingress proxy) so the ALLOWED_CLIENT_IPS check means something.
# --timeout-graceful-shutdown: finish in-flight requests, then exit before the
# Supervisor's 10-second stop timeout would kill the process.
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8099", \
     "--no-proxy-headers", "--timeout-graceful-shutdown", "8"]
