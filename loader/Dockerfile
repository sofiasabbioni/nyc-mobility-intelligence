# Build context is the project root (see docker-compose.yml)
FROM python:3.12-slim
WORKDIR /work
COPY loader/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY loader/ loader/
COPY sql/ sql/
WORKDIR /work/loader
CMD ["python", "run_loader.py"]
