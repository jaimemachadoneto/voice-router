# The router service image. It starts with no arguments and reads the
# environment of the container.
FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

# The scanner refuses an image with a fixable HIGH finding, and the base image
# ships Debian packages that carry one. Take the security updates of the base
# distribution, then remove the lists so the layer stays small.
RUN apt-get update \
    && apt-get -y upgrade \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src

RUN python -m pip install --no-cache-dir .

# The scanner refuses an image with a fixable HIGH or CRITICAL finding. These
# three arrive with the base image and its build tools, and every newer release
# closes a denial of service or a path traversal. Raise them after the install,
# so the resolver cannot pull the old ones back.
RUN python -m pip install --no-cache-dir --upgrade \
    "setuptools>=78.1.1" \
    "urllib3>=2.8.0" \
    "msgpack>=1.2.3"

RUN mkdir -p /data

EXPOSE 8000

CMD ["uvicorn", "voice_router.api:app_from_env", "--factory", "--host", "0.0.0.0", "--port", "8000"]
