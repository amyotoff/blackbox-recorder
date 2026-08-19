FROM python:3.11-slim

WORKDIR /app

# BlackBox has 0 runtime dependencies, install development dependencies for testing
RUN pip install --no-cache-dir pytest pytest-asyncio

# Copy project files
COPY pyproject.toml README.md LICENSE ./
COPY ai_blackbox_recorder/ ./ai_blackbox_recorder/
COPY tests/ ./tests/
COPY examples/ ./examples/

# Install package in editable mode
RUN pip install --no-cache-dir -e .

# Run test suite by default
CMD ["pytest", "-v"]
