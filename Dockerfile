# Dockerfile
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Security & size: install system deps needed by lxml, then clean
RUN apt-get update \
 && apt-get install -y --no-install-recommends libxml2 libxslt1.1 libxml2-utils ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY templates ./templates

ENV PORT=3428
EXPOSE 3428

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "3428"]

