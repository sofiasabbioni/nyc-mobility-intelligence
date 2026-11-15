# The loader container: downloads the TLC files and builds the three tables in PostgreSQL.
# Build context is the project root (see docker-compose.yml), so the scripts can stay where they are.
FROM python:3.12-slim

WORKDIR /app

# Install the libraries first: Docker caches this layer, so code changes rebuild fast.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY download_data.py load_data.py preprocessing_data_postgress.py cleaning_data_postgress.py analysis_data_postgres.py run_pipeline.py ./

CMD ["python", "run_pipeline.py"]
