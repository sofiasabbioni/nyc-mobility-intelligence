# Build context is the project root (see docker-compose.yml)
FROM python:3.12-slim
WORKDIR /work
COPY benchmark/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY benchmark/ benchmark/
COPY sql/ sql/
WORKDIR /work/benchmark
CMD ["python", "benchmark.py"]
