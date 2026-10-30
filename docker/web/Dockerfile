# syntax=docker/dockerfile:1.7
#
# TableFlow web image: the static Vue client behind nginx.
#
#   docker build -f docker/web/Dockerfile -t tableflow-web .
#
# nginx also proxies /api and /health to the API, so the browser talks to
# one origin and the API needs no CORS rule. The image runs as a non-root
# user and listens on 8080.

FROM nginxinc/nginx-unprivileged:1.27-alpine

ARG GIT_SHA=unknown
LABEL org.opencontainers.image.title="tableflow-web" \
      org.opencontainers.image.source="https://github.com/Wyco68/Restaurant_Reservation_System" \
      org.opencontainers.image.revision="${GIT_SHA}"

# Read by the template at container start (envsubst), so one image runs in
# every environment. API_BASE empty means same origin.
ENV API_UPSTREAM=http://api:8000 \
    API_BASE=""

COPY docker/web/security-headers.conf /etc/nginx/snippets/security-headers.conf
COPY docker/web/default.conf.template /etc/nginx/templates/default.conf.template
COPY frontend/ /usr/share/nginx/html/

EXPOSE 8080

HEALTHCHECK --interval=10s --timeout=3s --retries=3 \
    CMD ["wget", "-qO-", "http://127.0.0.1:8080/healthz"]
