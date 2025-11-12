FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Build deps for lxml (in case wheels lag for 3.14)
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      build-essential \
      gcc \
      libxml2-dev \
      libxslt1-dev \
      zlib1g-dev \
      ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --upgrade pip \
 && pip install -r requirements.txt

COPY app ./app
COPY templates ./templates
COPY EN-PL.dsl PL-ENG.dsl ./

ENV PORT=3428
EXPOSE 3428

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "3428"]


