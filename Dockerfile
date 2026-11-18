FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl python3 python3-venv \
    libasound2t64 libbsd0 libx11-6 libxau6 libxcb1 libxdmcp6 \
    libxext6 libxi6 libxrender1 libxtst6 zlib1g libfreetype6 fontconfig \
    && apt-get clean

WORKDIR /app
COPY requirements.txt .
RUN python3 -m venv .venv && .venv/bin/pip install --no-cache-dir -r requirements.txt
COPY scripts/setup-omr.sh scripts/setup-omr.sh
RUN bash scripts/setup-omr.sh
COPY app app
COPY web web
RUN useradd --create-home --uid 10001 musician
USER musician
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s \
    CMD curl --fail --silent http://127.0.0.1:8000/api/health || exit 1
CMD [".venv/bin/python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
