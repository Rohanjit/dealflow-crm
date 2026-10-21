# ---- Build the React app ----
FROM node:22-alpine AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---- Python app serving the API and the built frontend ----
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEALFLOW_DATA_DIR=/data \
    DEALFLOW_FRONTEND_DIST=/app/frontend/dist
WORKDIR /app/backend
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ ./
COPY --from=frontend /frontend/dist /app/frontend/dist
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh && useradd --create-home --uid 1000 dealflow && mkdir -p /data /keys && chown dealflow /data /keys && chmod 700 /keys
USER dealflow
VOLUME ["/data", "/keys"]
EXPOSE 8000
ENTRYPOINT ["/entrypoint.sh"]
CMD ["web"]
