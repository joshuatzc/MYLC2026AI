# ── Stage 1: Build dependencies ──────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /app

# Install dependencies into a separate layer for caching
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# ── Stage 2: Runtime image ────────────────────────────────────────────────────
FROM python:3.11-slim

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application source
COPY app/ ./app/
COPY scripts/ ./scripts/
COPY assets/ ./assets/

# Data directory for persistent SQLite database (mount a volume here)
RUN mkdir -p /data

# Ensure all files are readable regardless of source permissions (e.g. OneDrive sync)
RUN chmod -R 755 /app

# Run as non-root for security
RUN useradd -m botuser && chown -R botuser /app /data
USER botuser

EXPOSE 8000

# Production: no --reload, single worker is fine for SQLite
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
