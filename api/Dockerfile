# One image for both the API and the worker. They run different commands over the same
# code, and building twice would let them drift onto different dependency trees — which
# is the failure where a worker deserialises a report the API cannot render.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Dependencies before source, so editing a rule does not re-resolve the tree. --no-dev
# leaves ruff, mypy, pytest and deepeval out of the runtime image: they are the build's
# tools, not the service's.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-install-project --no-dev

COPY . .
RUN uv sync --locked --no-dev

ENV PATH="/app/.venv/bin:$PATH"

# fastembed downloads its ONNX model on first use. Pointed at a volume in compose so a
# restart does not re-download 200 MB, and declared here so the path exists either way.
ENV EMBEDDING_CACHE_DIR=/app/.cache/fastembed
RUN mkdir -p /app/.cache/fastembed

EXPOSE 8000

# Migrations then serve. The alternative is a separate one-shot migrate service, which
# is more correct with several API replicas and more moving parts with one — and one is
# what this deployment is (docs/deployment.md).
CMD ["sh", "-c", "alembic upgrade head && uvicorn specguard.api.app:app --host 0.0.0.0 --port 8000"]
