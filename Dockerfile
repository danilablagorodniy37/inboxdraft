FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    FASTEMBED_CACHE_PATH=/app/data/models

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install .

COPY knowledge ./knowledge
COPY inbox ./inbox
COPY evals ./evals

EXPOSE 8000
CMD ["inboxdraft", "demo", "--host", "0.0.0.0", "--port", "8000"]
