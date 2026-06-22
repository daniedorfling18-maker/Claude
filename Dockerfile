FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl bash \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src ./src
COPY scripts ./scripts
COPY inputs ./inputs

ARG INSTALL_POLYMARKET_SDK=false
RUN python -m pip install --upgrade pip \
    && pip install -e . \
    && if [ "${INSTALL_POLYMARKET_SDK}" = "true" ]; then pip install py-clob-client-v2; fi

RUN mkdir -p outputs/polymarket

CMD ["bash", "scripts/run_polymarket_agent.sh"]
