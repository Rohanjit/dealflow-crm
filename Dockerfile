# syntax=docker/dockerfile:1
# Dealflow in one image: the API and the built React app, served by gunicorn (decision 63, S6).
# Base images are pinned by digest; Dependabot proposes updates (.github/dependabot.yml).
ARG NODE_IMAGE=node:22-alpine@sha256:ce8d97f7cfc7c5386421eb932df20a29df99b1a7f469f6157d5c9956702ffadc
ARG PYTHON_IMAGE=python:3.12-slim@sha256:cb33f7f771ecec2ef1225cf39b159591a040391bd73e4c01f00fdfc4c47611d2

# ---- Build the React app (package-lock.json carries every package's hash) ----
FROM ${NODE_IMAGE} AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
# A company proxy that inspects https can pass its certificate: --secret id=ca_bundle,src=ca.pem
RUN --mount=type=secret,id=ca_bundle,required=false \
    if [ -f /run/secrets/ca_bundle ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/ca_bundle; fi; \
    npm ci --no-audit --no-fund --ignore-scripts
COPY frontend/ ./
RUN npm run build

# ---- Python packages, checked against the hashes in requirements.txt ----
FROM ${PYTHON_IMAGE} AS wheels
WORKDIR /w
COPY backend/requirements.txt ./
RUN --mount=type=secret,id=ca_bundle,required=false \
    if [ -f /run/secrets/ca_bundle ]; then export PIP_CERT=/run/secrets/ca_bundle; fi; \
    pip wheel --no-cache-dir --require-hashes --no-deps -r requirements.txt -w /wheels

# ---- The image that runs ----
FROM ${PYTHON_IMAGE}
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEALFLOW_DATA_DIR=/data \
    DEALFLOW_KEYS_DIR=/keys \
    DEALFLOW_FRONTEND_DIST=/app/frontend/dist
WORKDIR /app/backend
COPY --from=wheels /wheels /wheels
# Install offline from the checked wheels, then remove pip itself: nothing installs packages at run time.
RUN pip install --no-cache-dir --no-index --no-deps /wheels/*.whl \
    && rm -rf /wheels \
    && python -m pip uninstall -y pip setuptools wheel >/dev/null 2>&1 || true
COPY backend/ ./
RUN rm -rf */tests core/tests mail/tests vault/tests bandit.yaml requirements.in \
    && python -m compileall -q /app/backend
COPY --from=frontend /frontend/dist /app/frontend/dist
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod 0555 /entrypoint.sh \
    && useradd --create-home --uid 1000 --shell /usr/sbin/nologin dealflow \
    && mkdir -p /data /keys && chown dealflow /data /keys && chmod 700 /keys
USER dealflow
VOLUME ["/data", "/keys"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD ["python", "/app/backend/docker_health.py"]
ENTRYPOINT ["/entrypoint.sh"]
CMD ["web"]
