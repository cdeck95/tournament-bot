#!/bin/bash

# Function to handle errors and clean up
cleanup() {
  echo "Cleaning up any stray Chrome processes..."
  pkill -f chrome
  exit 1
}

# Trap Ctrl+C and errors
trap cleanup SIGINT SIGTERM ERR

# Make sure the virtual environment exists
if [ ! -d "venv" ]; then
  echo "Creating virtual environment..."
  python -m venv venv
fi

# Activate virtual environment
source venv/bin/activate || source venv/Scripts/activate

# Install requirements
echo "Installing/updating requirements..."
pip install -r requirements.txt

# Run the bot
echo "Starting tournament bot..."
python script.py

# Deactivate virtual environment on exit
deactivate
