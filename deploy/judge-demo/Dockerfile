FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/src
WORKDIR /app
COPY deploy/judge-demo/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
RUN useradd --uid 10001 --create-home demo
COPY procurement-engine/src /app/src
COPY procurement-engine/config /app/config
RUN test -s /app/config/market_reference_snapshot_v1.json && \
    test -s /app/config/market_reference_manifest_v1.json && \
    test -s /app/config/market_reference_registry_v1.json && \
    test -s /app/config/market_reference_sources_v1.json
COPY procurement-engine/demo_fixtures /app/demo_fixtures
COPY apps/web/dist /app/web
ENV PROCURE_CONFIG_DIR=/app/config PROCURE_DATA_DIR=/tmp/closed-source-demo DEMO_WEB_DIR=/app/web
USER 10001
EXPOSE 8080
CMD ["python", "-m", "uvicorn", "procure.demo.app:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips", "*"]
