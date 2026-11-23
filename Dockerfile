# ForgeEmber runtime image. No secrets are baked in: pass MODEL_API_KEY at run time only for live calls.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /uvx /bin/

WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
RUN uv sync --locked --no-dev

RUN useradd --create-home --uid 10001 app \
    && mkdir -p /app/var /app/traces /app/data \
    && chown -R app:app /app
USER app

ENTRYPOINT ["/app/.venv/bin/forgeember"]
CMD ["budget"]
