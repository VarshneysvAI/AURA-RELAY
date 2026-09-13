#!/bin/bash
# AURA Relay Development Script

set -e

echo "=== AURA Relay Development Setup ==="

# Create virtual environment if it doesn't exist
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python -m venv venv
fi

# Activate virtual environment
source venv/bin/activate

# Install dependencies
echo "Installing dependencies..."
pip install -r requirements.txt

# Install Playwright browsers
echo "Installing Playwright Chromium..."
python -m playwright install chromium

# Run the application
echo "Starting AURA Relay server..."
python main.py
