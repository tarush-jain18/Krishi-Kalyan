FROM python:3.11-slim

WORKDIR /app

# System dependencies
RUN apt-get update && apt-get install -y \
    git \
    wget \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project
COPY . .

# Download models during IMAGE BUILD
RUN python scripts/download_models.py

# Railway provides PORT automatically
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT}