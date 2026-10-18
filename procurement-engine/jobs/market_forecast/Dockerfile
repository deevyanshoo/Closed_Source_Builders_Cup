FROM python:3.11.15-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/src \
    OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 MKL_NUM_THREADS=2
WORKDIR /app
COPY procurement-engine/jobs/market_forecast/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
RUN useradd --uid 10002 --create-home market
COPY procurement-engine/src /app/src
COPY procurement-engine/jobs/market_forecast/bundle /app/bundle
COPY procurement-engine/jobs/market_forecast/reference_snapshot.json /app/reference_snapshot.json
COPY procurement-engine/jobs/market_forecast/runner.py /app/runner.py
COPY procurement-engine/jobs/market_forecast/Dockerfile /app/Dockerfile
USER 10002
ENTRYPOINT ["python", "/app/runner.py"]
CMD ["--verify-only"]
