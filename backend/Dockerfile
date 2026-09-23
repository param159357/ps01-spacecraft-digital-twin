# Python 3.14 matches the verified local development toolchain; every pinned
# dependency (including numpy and scikit-learn) publishes cp314 wheels.
FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependency layer first so application edits do not reinstall the world.
COPY pyproject.toml ./
RUN pip install --upgrade pip && pip install .

COPY app ./app
COPY migrations ./migrations
COPY alembic.ini ./

# Run as a non-root user.
RUN useradd --create-home --uid 10001 ps01 && chown -R ps01:ps01 /app
USER ps01

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
