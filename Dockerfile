FROM python:3.12-slim
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends gcc git \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ .
COPY version.json .

# Create a non-root user; pre-create /data so the volume mount is owned correctly
RUN useradd -r -u 10001 -g users evtracker \
    && mkdir -p /data \
    && chown -R evtracker:users /app /data

VOLUME ["/data"]
EXPOSE 8080
ENV TZ=Europe/Berlin

USER evtracker

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/api/health')" || exit 1

# Single gunicorn worker with threads — background tracker threads must not be duplicated
CMD ["gunicorn", "server:app", "-c", "gunicorn.conf.py"]
