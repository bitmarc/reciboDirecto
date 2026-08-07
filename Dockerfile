# syntax=docker/dockerfile:1
#
# El entorno de desarrollo es idéntico al de producción a propósito: WeasyPrint
# renderiza a través de Pango, y una versión distinta de Pango cambia tipografías
# y saltos de línea. Un recibo que se ve bien aquí se ve igual en Azure.

FROM python:3.12-slim-bookworm AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# Nativas de WeasyPrint. WeasyPrint >= 53 ya no depende de Cairo (usa pydyf),
# pero sí de Pango para el trazado de texto y de libjpeg/openjp2 para las
# imágenes del logo y las firmas.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
        libjpeg62-turbo \
        libopenjp2-7 \
        shared-mime-info \
        fonts-dejavu-core \
        fonts-liberation2 \
        curl \
    && rm -rf /var/lib/apt/lists/*


FROM base AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libffi-dev \
    && rm -rf /var/lib/apt/lists/*

RUN pip install "poetry==2.1.2" poetry-plugin-export

WORKDIR /build
COPY pyproject.toml poetry.lock* ./

# El venv vive fuera de /app para que el bind mount del código en desarrollo
# no lo tape.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

ARG INSTALL_DEV=false
RUN if [ "$INSTALL_DEV" = "true" ]; then \
        poetry export --without-hashes --with dev -f requirements.txt -o /tmp/req.txt; \
    else \
        poetry export --without-hashes -f requirements.txt -o /tmp/req.txt; \
    fi \
    && pip install --no-cache-dir -r /tmp/req.txt


FROM base AS runtime

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH=/app/src

WORKDIR /app
COPY alembic.ini ./
COPY migrations ./migrations
COPY src ./src

RUN useradd --create-home --uid 1000 app && chown -R app:app /app
USER app

EXPOSE 8000
CMD ["uvicorn", "receipt.main:app", "--host", "0.0.0.0", "--port", "8000"]
