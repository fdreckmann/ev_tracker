FROM python:3.12-slim
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends gcc git gosu \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ .
COPY version.json .
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

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/api/health')" || exit 1

ENTRYPOINT ["docker-entrypoint.sh"]
