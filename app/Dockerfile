# The app container: the Streamlit dashboard with the interactive map.
FROM python:3.12-slim

WORKDIR /app

# Libraries first: Docker caches this layer, so changing dashboard.py rebuilds in seconds.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY dashboard.py .

EXPOSE 8501

# 0.0.0.0 = accept connections from outside the container (your browser on the laptop).
CMD ["streamlit", "run", "dashboard.py", "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true"]
