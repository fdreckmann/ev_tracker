FROM python:3.12-slim
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends gcc git gosu \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ .
COPY version.json .
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

# Create non-root user; /data is created here but ownership is fixed at runtime
# by the entrypoint (existing volumes may be owned by root from prior versions).
RUN useradd -r -u 10001 -g users evtracker \
    && mkdir -p /data \
    && chown -R evtracker:users /app

VOLUME ["/data"]
EXPOSE 8080
ENV TZ=Europe/Berlin

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/api/health')" || exit 1

# Entrypoint runs as root, fixes /data permissions, then drops to evtracker
ENTRYPOINT ["docker-entrypoint.sh"]
