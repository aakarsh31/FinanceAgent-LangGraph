FROM python:3.11-slim

WORKDIR /app

# Install Node.js for React build
RUN apt-get update && apt-get install -y curl && \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y nodejs && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Install uv
RUN pip install uv

# Copy dependency files first (layer caching)
COPY pyproject.toml .
COPY uv.lock .

# Install Python dependencies
RUN uv sync --frozen

# Copy source code
COPY . .

# Build React frontend
RUN cd frontend-react && npm install && npm run build

# Expose port
EXPOSE 8000

# No CMD here — Railway start command controls this per-service:
# web:    uv run uvicorn app:app --host 0.0.0.0 --port 8000
# worker: uv run python worker.py