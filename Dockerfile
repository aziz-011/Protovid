# Protovid web app: Word protocol -> training video
# Build and run:  docker compose up -d --build      then open http://localhost:8000
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
# ffmpeg (video), Festival + eSpeak/MBROLA (offline voices), OSMesa (3D without a GPU), fonts, Python
RUN sed -i 's/^Components: main restricted$/Components: main restricted universe multiverse/' \
        /etc/apt/sources.list.d/ubuntu.sources || true \
    && apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-venv python3-pip ca-certificates \
        ffmpeg festival festvox-us-slt-hts espeak-ng mbrola mbrola-us2 mbrola-fr1 \
        libosmesa6 libegl1 libgl1 libxrender1 \
        fonts-inter fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers
COPY pyproject.toml README.md ./
COPY protovid ./protovid
RUN pip install --no-cache-dir ".[web,3d]" \
    && playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*

ENV PROTOVID_DATA=/data \
    HOST=0.0.0.0 \
    PORT=8000 \
    PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 8000
CMD ["protovid-web"]
