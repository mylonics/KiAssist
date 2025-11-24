#!/bin/bash
# Startup script for KiAssist (Unix-like systems)

set -e

echo "Starting KiAssist..."

# Create virtual environment if it doesn't exist
if [ ! -d "venv" ]; then
    echo "Creating Python virtual environment..."
    python3 -m venv venv
fi

# Activate virtual environment
source venv/bin/activate

# Check if dist directory exists
if [ ! -d "dist" ]; then
    echo "Building frontend..."
    npm run build
fi

# Install Python dependencies if needed
echo "Checking Python dependencies..."
cd python-lib
if ! pip install -e . > /dev/null 2>&1; then
    echo "Warning: Failed to install Python dependencies. Trying without --editable..."
    pip install .
fi
cd ..

# Run the Python application
echo "Launching application..."
python -m kiassist_utils.main
