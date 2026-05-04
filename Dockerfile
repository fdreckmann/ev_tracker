FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends gcc git \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ .

VOLUME ["/data"]
EXPOSE 8080
ENV TZ=Europe/Berlin

CMD ["python", "server.py"]
