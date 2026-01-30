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

# Install Cloudflare Tunnel
RUN curl -L --output cloudflared.deb https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb && \
  dpkg -i cloudflared.deb && \
  rm cloudflared.deb

# Create start script
RUN echo '#!/bin/bash\n\
  if [ -n "$CLOUDFLARE_TOKEN" ]; then\n\
  echo "🚀 Starting Cloudflare Tunnel..."\n\
  cloudflared tunnel run --token $CLOUDFLARE_TOKEN &\n\
  fi\n\
  echo "🚀 Starting Bot..."\n\
  python main.py' > /app/start.sh && chmod +x /app/start.sh

# Run the bot with tunnel support
CMD ["/app/start.sh"]
