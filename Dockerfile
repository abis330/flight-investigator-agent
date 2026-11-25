FROM python:3.11-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 FLIGHTLAKE_ROOT=/data/lake INCIDENTS_PATH=/data/incidents.json KB_DIR=/app/kb
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml README.md ./
COPY src ./src
COPY kb ./kb
RUN pip install --no-cache-dir ".[anthropic]"
EXPOSE 8090 8765
CMD ["uvicorn", "flightinvestigator.api:app", "--host", "0.0.0.0", "--port", "8090"]
