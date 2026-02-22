FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FFMPEG_PATH=/usr/bin/ffmpeg

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
  ffmpeg \
  libopus0 \
  libopus-dev \
  libsodium-dev \
  libffi-dev \
  gcc \
  g++ \
  make \
  pkg-config \
  git \
  && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements first for caching
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
  && pip install --no-cache-dir -r requirements.txt

# Copy all files
COPY . .

# External persistent storage mount point
RUN mkdir -p /data
VOLUME ["/data"]

# Expose port
EXPOSE 7860

# Run the bot
CMD ["python", "main.py"]
