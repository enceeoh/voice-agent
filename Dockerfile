# ---- Stage 1: build the virtualenv ----
FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev
COPY . .
RUN uv sync --frozen --no-dev

# ---- Stage 2: runtime ----
FROM python:3.12-slim
RUN useradd --create-home appuser
WORKDIR /app
COPY --from=builder /app /app
ENV PATH="/app/.venv/bin:$PATH"
USER appuser
EXPOSE 8000
CMD ["uvicorn", "voice_agent.api:app", "--host", "0.0.0.0", "--port", "8000"]
