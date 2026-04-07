FROM python:3.12-slim-bookworm

# Pass at build time so logs show what was deployed: docker build --build-arg GIT_SHA=$(git rev-parse --short HEAD) .
ARG GIT_SHA=unknown
ENV GIT_SHA=${GIT_SHA}

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /data

CMD ["python", "bot.py"]
