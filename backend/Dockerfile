# Production-ready Python 3.11-slim container for HeritageLens AI on Google Cloud Run
FROM python:3.11-slim

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080

# Set working directory
WORKDIR /app

# Install system dependencies if needed (e.g. for image processing / libGL)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy and install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source and data
COPY . .

# Expose Google Cloud Run standard port
EXPOSE 8080

# Run FastAPI app with Uvicorn
CMD exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}
