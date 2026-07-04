FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl bash \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md polymarket_predictive_config.example.yaml ./
COPY src ./src
COPY scripts ./scripts
COPY inputs ./inputs

ARG INSTALL_POLYMARKET_SDK=false
ARG INSTALL_SCRAPER=false
ARG PM_IMAGE_BUILD_SHA=unknown
ENV PM_IMAGE_BUILD_SHA=${PM_IMAGE_BUILD_SHA}
RUN python -m pip install --upgrade pip \
    && if [ "${INSTALL_SCRAPER}" = "true" ]; then pip install -e ".[scraper]"; else pip install -e .; fi \
    && if [ "${INSTALL_POLYMARKET_SDK}" = "true" ]; then pip install py-clob-client-v2; fi \
    && if [ "${INSTALL_SCRAPER}" = "true" ]; then python -m playwright install chromium --with-deps; fi

RUN mkdir -p outputs/polymarket work/polymarket

CMD ["python", "scripts/run_polymarket_live_paper_loop.py", "--iterations", "1", "--optimize-model"]
