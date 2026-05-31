FROM python:3.12-slim
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends gcc git gosu \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ .
COPY version.json .
COPY build-info.json .
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

# Create non-root user (UID 10001) with a writable home.
# At runtime the effective user is determined by either:
#   - user: "PUID:PGID" in docker-compose.yml  (recommended, Mode A)
#   - PUID/PGID env vars with root start + gosu (Mode B)
RUN useradd -r -u 10001 -g users -d /home/evtracker evtracker \
    && mkdir -p /data /home/evtracker \
    && chown -R evtracker:users /app /home/evtracker

VOLUME ["/data"]
EXPOSE 8080
ENV TZ=Europe/Berlin
ENV HOME=/home/evtracker

# Build-time metadata injected by GitHub Actions (see .github/workflows/docker-build.yml).
# Falls back to "unknown" when building locally without --build-arg.
ARG EV_TRACKER_VERSION=unknown
ARG EV_TRACKER_BUILD=unknown
ARG EV_TRACKER_CHANNEL=unknown
ARG EV_TRACKER_COMMIT=unknown
ARG EV_TRACKER_BRANCH=unknown
ARG EV_TRACKER_IMAGE_TAG=unknown

ENV EV_TRACKER_VERSION=$EV_TRACKER_VERSION
ENV EV_TRACKER_BUILD=$EV_TRACKER_BUILD
ENV EV_TRACKER_CHANNEL=$EV_TRACKER_CHANNEL
ENV EV_TRACKER_COMMIT=$EV_TRACKER_COMMIT
ENV EV_TRACKER_BRANCH=$EV_TRACKER_BRANCH
ENV EV_TRACKER_IMAGE_TAG=$EV_TRACKER_IMAGE_TAG

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/api/health')" || exit 1

ENTRYPOINT ["docker-entrypoint.sh"]
