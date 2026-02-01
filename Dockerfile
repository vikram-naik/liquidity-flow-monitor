FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Copy requirements and install
COPY requirements.txt .
# Note: We install all requirements, but Selenium won't be used by API/Dashboard.
# System dependencies for Selenium are removed.
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY . .

# Create data directory for SQLite volume
RUN mkdir -p /app/data

# Environment variables
ENV PYTHONPATH=/app
ENV DB_PATH=/app/data/liquidity_monitor.db

# Expose ports (8000 for API, 8501 for Streamlit)
EXPOSE 8000
EXPOSE 8501

# Command is specified in docker-compose
