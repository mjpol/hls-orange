#!/usr/bin/env bash
set -euo pipefail

# Arrancar nginx con nuestra config
nginx -c /opt/hls-orange/config/nginx.conf

echo "nginx arrancado en :8080"

# Arrancar el streamer HLS
exec /opt/hls-orange/scripts/start-hls.sh
