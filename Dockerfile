FROM python:3.14-alpine AS python-base

FROM python-base AS requirements
WORKDIR /build
ARG UV_VERSION=0.11.7
COPY pyproject.toml uv.lock ./
RUN pip install --only-binary=:all: --no-cache-dir "uv==${UV_VERSION}"
RUN uv export --frozen --no-dev --output-file service.txt && \
    uv export --frozen --output-file tests.txt

FROM python-base AS base
WORKDIR /app
ARG PIP_VERSION=26.3
RUN apk add --no-cache --upgrade openssh-client 'libcrypto3>=3.5.9-r1' 'libssl3>=3.5.9-r1' 'zlib>=1.4-r0' && \
    pip install --only-binary=:all: --no-cache-dir --upgrade "pip==${PIP_VERSION}" && \
    addgroup -S stellar && adduser -S -D -H -G stellar stellar
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app
COPY src/ ./src/
COPY alembic.ini ./
COPY etc/docker-entrypoint ./docker-entrypoint
ENTRYPOINT ["/bin/sh", "/app/docker-entrypoint"]

FROM base AS tests
RUN --mount=type=bind,from=requirements,source=/build,target=/requirements \
    pip install --only-binary=:all: --no-cache-dir --no-deps --require-hashes -r /requirements/tests.txt
COPY pyproject.toml ./
ENV RUFF_CACHE_DIR=/tmp/ruff MYPY_CACHE_DIR=/tmp/mypy
USER stellar

FROM base AS service
# Package installers are only needed while building the service image.
RUN --mount=type=bind,from=requirements,source=/build,target=/requirements \
    pip install --only-binary=:all: --no-cache-dir --no-deps --require-hashes -r /requirements/service.txt && \
    pip uninstall --yes pip && \
    rm -f /usr/local/lib/python3.14/ensurepip/_bundled/pip-*.whl
ENV APP_PORT=8000
USER stellar
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import http.client, os; c = http.client.HTTPConnection('127.0.0.1', int(os.environ['APP_PORT']), timeout=3); c.request('GET', '/login'); exit(0 if c.getresponse().status == 200 else 1)"
