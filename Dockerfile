# The router service image. It starts with no arguments and reads the
# environment of the container.
FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src

RUN python -m pip install --no-cache-dir .

RUN mkdir -p /data

EXPOSE 8000

CMD ["uvicorn", "voice_router.api:app_from_env", "--factory", "--host", "0.0.0.0", "--port", "8000"]
