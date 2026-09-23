# Multi-stage Dockerfile for AgentFlow Backend
FROM python:3.11-slim as builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Final runtime image
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

RUN addgroup --system agentflow \
    && adduser --system --ingroup agentflow agentflow \
    && mkdir -p /app/workspace_data

ARG INSTALL_CRAWLER_BROWSER=false
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN if [ "$INSTALL_CRAWLER_BROWSER" = "true" ]; then \
    python -m playwright install --with-deps chromium \
    && chown -R agentflow:agentflow /ms-playwright \
    && rm -rf /var/lib/apt/lists/*; \
    fi

COPY backend/app /app/app
COPY backend/alembic /app/alembic
COPY alembic.ini /app/alembic.ini

RUN chown -R agentflow:agentflow /app

USER agentflow

EXPOSE 8000

ENV PYTHONPATH=/app
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
