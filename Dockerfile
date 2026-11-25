FROM python:3.14-slim AS base
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg curl ca-certificates \
  && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app

FROM base AS deps
COPY pyproject.toml README.md ./
COPY server ./server
RUN uv sync --no-dev --no-install-project

FROM base AS runtime
COPY --from=deps /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1
COPY server ./server
COPY frontend/dist ./frontend/dist
COPY config.example.yaml ./config.example.yaml
COPY scripts ./scripts
RUN useradd -r -u 10001 smartcctv && mkdir -p /app/data /app/models && chown -R smartcctv:smartcctv /app
USER smartcctv
EXPOSE 8300
CMD ["uvicorn", "server.app:app", "--host", "0.0.0.0", "--port", "8300"]
