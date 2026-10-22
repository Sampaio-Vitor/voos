FROM python:3.14-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.12.10 /uv /bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project
COPY src ./src
RUN uv sync --locked --no-dev --no-editable

FROM python:3.14-slim
COPY --from=build /app/.venv /app/.venv
ARG APP_REVISION=dev
LABEL org.opencontainers.image.source="https://github.com/Sampaio-Vitor/voos"
LABEL org.opencontainers.image.description="MCP de preços de voos do Google Flights"
LABEL org.opencontainers.image.revision=$APP_REVISION
ENV PATH=/app/.venv/bin:$PATH VOOS_REVISAO=$APP_REVISION VOOS_HOST=0.0.0.0 VOOS_PORT=8790
USER nobody
EXPOSE 8790
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
  CMD ["python", "-c", "import urllib.request as u; u.urlopen('http://127.0.0.1:8790/healthz', timeout=2)"]
CMD ["voos"]
