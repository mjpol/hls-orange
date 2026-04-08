FROM ubuntu:24.04

# Instalar ffmpeg y nginx sin interacción
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    nginx \
    curl \
    iproute2 \
    && rm -rf /var/lib/apt/lists/*

# Directorios
RUN mkdir -p /opt/hls-orange/{hls,config,logs,scripts}

# Copiar configuración
COPY config/nginx.conf  /opt/hls-orange/config/nginx.conf
COPY scripts/start-hls.sh /opt/hls-orange/scripts/start-hls.sh
COPY scripts/entrypoint.sh /opt/hls-orange/scripts/entrypoint.sh
COPY hls/index.html /opt/hls-orange/hls/index.html

RUN chmod +x /opt/hls-orange/scripts/*.sh

EXPOSE 8080

ENTRYPOINT ["/opt/hls-orange/scripts/entrypoint.sh"]
