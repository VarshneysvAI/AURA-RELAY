FROM python:3.11-slim

WORKDIR /app

# Install system dependencies & dumb-init for clean process signals
RUN apt-get update && apt-get install -y \
    wget \
    curl \
    ca-certificates \
    dumb-init \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright Chromium with all required system OS libraries
RUN python -m playwright install --with-deps chromium

# Copy application code
COPY . .

# Create runtime directories for logs & screenshots
RUN mkdir -p /app/runtime/screenshots

# Production environment defaults for Azure / Container deployment
ENV HEADLESS=true
ENV MODE=live
ENV APP_HOST=0.0.0.0
ENV APP_PORT=8000

# Expose port
EXPOSE 8000

# Use dumb-init to prevent zombie chromium processes
ENTRYPOINT ["/usr/bin/dumb-init", "--"]

# Run the application
CMD ["python", "main.py"]


