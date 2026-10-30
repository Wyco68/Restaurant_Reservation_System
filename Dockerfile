# syntax=docker/dockerfile:1.7
#
# TableFlow API image.
#
#   docker build -t tableflow-api .
#
# Two stages: dependencies are installed into a virtualenv in the first one
# and only that virtualenv is copied into the second, so the runtime image
# has no pip cache and no build tools.

ARG PYTHON_VERSION=3.12

FROM python:${PYTHON_VERSION}-slim AS build

ENV PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH

# Copied on its own so the dependency layer is reused until the file changes.
COPY requirements-runtime.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements-runtime.txt


FROM python:${PYTHON_VERSION}-slim AS runtime

ARG GIT_SHA=unknown
ARG BUILD_DATE=unknown
LABEL org.opencontainers.image.title="tableflow-api" \
      org.opencontainers.image.source="https://github.com/Wyco68/Restaurant_Reservation_System" \
      org.opencontainers.image.revision="${GIT_SHA}" \
      org.opencontainers.image.created="${BUILD_DATE}"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH \
    APP_VERSION=${GIT_SHA}

# Fixed UID so Kubernetes can check runAsNonRoot without a lookup.
RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app

WORKDIR /app
COPY --from=build /opt/venv /opt/venv
COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
COPY scripts ./scripts

USER 10001
EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"]

# One process per container: scale with replicas, not workers. The
# migration phase is per process (app/migration.py), so workers could
# disagree about it.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--timeout-graceful-shutdown", "20"]
