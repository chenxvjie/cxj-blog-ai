FROM python:3.14-slim AS base
WORKDIR /app
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
COPY requirements.txt ./
RUN pip install --no-cache-dir --index-url "$PIP_INDEX_URL" -r requirements.txt && useradd --uid 10001 --create-home app
COPY app/ ./app/
COPY sql/ ./sql/
COPY scripts/ ./scripts/
USER app

FROM base AS verify
COPY tests/ ./tests/
COPY deploy/ ./deploy/
COPY pyproject.toml ./
RUN python -m ruff check --no-cache app scripts tests deploy && python -m pytest -q -o cache_dir=/tmp/pytest-cache

FROM base AS runtime
ARG VCS_REF=unknown
LABEL org.opencontainers.image.revision=$VCS_REF
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--loop", "app.loop:loop_factory"]
