FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
  ffmpeg \
  libopus0 \
  libopus-dev \
  git \
  && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements first for caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy all files
COPY . .

# Expose port
EXPOSE 7860

# Run the bot
CMD ["python", "main.py"]
