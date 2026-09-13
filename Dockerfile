FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    wget \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright Chromium with all required system OS libraries
RUN python -m playwright install --with-deps chromium

# Copy application code
COPY . .

# Production environment defaults for Azure / Container deployment
ENV HEADLESS=true
ENV MODE=live
ENV APP_HOST=0.0.0.0
ENV APP_PORT=8000

# Expose port
EXPOSE 8000

# Run the application
CMD ["python", "main.py"]

