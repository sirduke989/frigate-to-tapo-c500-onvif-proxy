# Use Python 3.12 slim image for smaller size
FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Install system dependencies required for lxml and zeep
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    gcc \
    libxml2-dev \
    libxslt-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better Docker layer caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY *.py ./
# Copy config directory (if present) into the image
# This places any config files at /app/config/ so they match the
# docker-compose bind mount `./config:/app/config`.
COPY config/ /app/config/

# Expose the proxy server port
EXPOSE 2020-2029

# Run the proxy server
CMD ["python3", "onvif_proxy.py"]
