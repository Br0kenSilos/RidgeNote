# syntax=docker/dockerfile:1

FROM node:24-bookworm-slim@sha256:b31e7a42fdf8b8aa5f5ed477c72d694301273f1069c5a2f71d53c6482e99a2fc AS frontend
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY core/static/core ./core/static/core
COPY tsconfig.json vite.config.ts ./
RUN npm run build

FROM python:3.13.14-slim-bookworm@sha256:fcbd8dfc2605ba7c2eca646846c5e892b2931e41f6227985154a596f26ab8ed7 AS runtime

ARG IMAGE_VERSION="0.0.0-dev"
ARG IMAGE_REVISION="unknown"
ARG IMAGE_CREATED="unknown"

LABEL org.opencontainers.image.title="RidgeNote" \
      org.opencontainers.image.description="Self-hosted, private organized notes application" \
      org.opencontainers.image.source="https://github.com/Br0kenSilos/RidgeNote" \
      org.opencontainers.image.revision="${IMAGE_REVISION}" \
      org.opencontainers.image.version="${IMAGE_VERSION}" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.created="${IMAGE_CREATED}"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install --no-install-recommends -y libpq5 \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --system ridgenote \
    && useradd --system --gid ridgenote --home-dir /app --create-home ridgenote

WORKDIR /app

COPY requirements/base.txt /tmp/requirements-base.txt
RUN pip install --require-hashes -r /tmp/requirements-base.txt

COPY manage.py ./
COPY ridgenote ./ridgenote
COPY accounts ./accounts
COPY notes ./notes
COPY core ./core
COPY --from=frontend /app/core/static/core/dist ./core/static/core/dist
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

# The image build never
# has a real RIDGENOTE_SECRET_KEY (or any deployment env at all) --
# only the eventual container run does. DEBUG stays at its normal
# build-time default (false) here -- a disposable, obviously-non-secret
# build-only key is supplied instead, scoped to this one RUN instruction
# only (not an ENV, so it never persists into the built image or its
# runtime environment), purely to satisfy the production-SECRET_KEY
# safety check during `collectstatic`, which never serves a request or
# touches sessions and so has no real use for SECRET_KEY at all. The
# actual runtime container always uses the real RIDGENOTE_SECRET_KEY
# from its own environment/.env, never this value.
RUN RIDGENOTE_SECRET_KEY=ridgenote-build-only-collectstatic-key-not-for-runtime \
    python manage.py collectstatic --noinput \
    && chown -R ridgenote:ridgenote /app \
    && chmod 0755 /usr/local/bin/docker-entrypoint.sh

USER ridgenote

EXPOSE 8000

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["gunicorn", "ridgenote.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2", "--threads", "2", "--timeout", "30", "--graceful-timeout", "30", "--access-logfile", "-", "--error-logfile", "-", "--access-logformat", "%(h)s %(l)s %(u)s %(t)s \"%(m)s\" %(s)s %(b)s %(L)s \"%(a)s\""]
