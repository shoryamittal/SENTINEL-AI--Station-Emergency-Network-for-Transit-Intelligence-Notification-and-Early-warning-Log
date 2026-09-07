# Build stage
FROM python:3.11-slim AS builder

WORKDIR /app

# Install system dependencies for OpenCV and build tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --user --no-cache-dir -r requirements.txt

# Final stage
FROM python:3.11-slim

# Create a non-root user
RUN useradd -m -s /bin/bash sentinel_user

# Install runtime dependencies for OpenCV
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy dependencies from builder
COPY --from=builder /root/.local /home/sentinel_user/.local
ENV PATH=/home/sentinel_user/.local/bin:$PATH

# Copy application code
COPY . /app

# Change ownership of the app directory to the non-root user
RUN chown -R sentinel_user:sentinel_user /app

# Switch to non-root user
USER sentinel_user

# Expose port
EXPOSE 5000

# Add healthcheck
HEALTHCHECK --interval=30s --timeout=30s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:5000/ || exit 1

# Run the application
CMD ["python", "deploy.py"]
