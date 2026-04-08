#!/usr/bin/env bash
# =============================================================================
# Instala hls-orange (streamer) y hls-orange-panel (panel web) como servicios
# Uso: sudo bash /opt/hls-orange/scripts/install-services.sh
# =============================================================================
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "ERROR: ejecutar como root (sudo)"
    exit 1
fi

PANEL_PORT="${PANEL_PORT:-9090}"

echo "=== Instalando servicios systemd ==="

# --- Servicio: streamer HLS ---
cat > /etc/systemd/system/hls-orange.service << 'EOF'
[Unit]
Description=HLS Orange — Multicast to HLS streamer
After=network.target hls-orange-panel.service
Wants=hls-orange-panel.service

[Service]
Type=simple
User=root
EnvironmentFile=-/opt/hls-orange/config/hls.env
ExecStartPre=/bin/bash -c 'nginx -c /opt/hls-orange/config/nginx.conf -t && (nginx -c /opt/hls-orange/config/nginx.conf || true)'
ExecStart=/opt/hls-orange/scripts/start-hls.sh
ExecStopPost=/usr/sbin/nginx -s stop 2>/dev/null || true
Restart=on-failure
RestartSec=3
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

# --- Servicio: panel web ---
cat > /etc/systemd/system/hls-orange-panel.service << EOF
[Unit]
Description=HLS Orange — Panel de configuracion web
After=network.target

[Service]
Type=simple
User=root
Environment=PANEL_PORT=${PANEL_PORT}
ExecStart=/usr/bin/python3 /opt/hls-orange/panel/server.py
Restart=always
RestartSec=2
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable hls-orange-panel
systemctl enable hls-orange

echo ""
echo "=== Servicios instalados ==="
echo ""
echo "  Panel web  : http://$(hostname -I | awk '{print $1}'):${PANEL_PORT}"
echo "  HLS stream : http://$(hostname -I | awk '{print $1}'):8080/hls/live/master.m3u8"
echo ""
echo "Comandos:"
echo "  systemctl start   hls-orange-panel   # arrancar panel"
echo "  systemctl start   hls-orange          # arrancar streamer"
echo "  systemctl status  hls-orange"
echo "  journalctl -fu    hls-orange          # logs en tiempo real"
echo ""

read -rp "Arrancar los servicios ahora? [S/n] " RESP
if [[ "${RESP,,}" != "n" ]]; then
    systemctl start hls-orange-panel
    echo "Panel arrancado en http://$(hostname -I | awk '{print $1}'):${PANEL_PORT}"
    echo "(Configura el stream desde el panel y luego pulsa 'Instalar servicio' + 'Iniciar')"
fi
