#!/usr/bin/env bash
# =============================================================================
# HLS Orange — Instalador automático
# Uso: sudo bash install.sh
# =============================================================================
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
ok()   { echo -e "${GREEN}  ✔ $*${NC}"; }
warn() { echo -e "${YELLOW}  ⚠ $*${NC}"; }
err()  { echo -e "${RED}  ✘ $*${NC}"; exit 1; }

INSTALL_DIR="/opt/hls-orange"
PANEL_PORT="${PANEL_PORT:-9090}"
HLS_PORT="${HLS_PORT:-8008}"

# ---------------------------------------------------------------------------
echo ""
echo "============================================"
echo "   HLS Orange — Instalador"
echo "============================================"
echo ""

[[ $EUID -ne 0 ]] && err "Ejecutar como root: sudo bash install.sh"

# ---------------------------------------------------------------------------
# 1. Copiar ficheros al destino
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ "$SCRIPT_DIR" != "$INSTALL_DIR" ]]; then
    echo "→ Copiando ficheros a $INSTALL_DIR ..."
    mkdir -p "$INSTALL_DIR"
    cp -r "$SCRIPT_DIR"/. "$INSTALL_DIR/"
    ok "Ficheros copiados"
else
    ok "Ficheros ya en $INSTALL_DIR"
fi

# ---------------------------------------------------------------------------
# 2. Dependencias del sistema
# ---------------------------------------------------------------------------
echo ""
echo "→ Comprobando dependencias ..."

apt_install() {
    dpkg -s "$1" &>/dev/null && ok "$1 ya instalado" || {
        warn "$1 no encontrado — instalando ..."
        apt-get install -y "$1" -qq && ok "$1 instalado"
    }
}

apt-get update -qq
apt_install nginx
apt_install python3
apt_install ffmpeg

# NVIDIA drivers / NVENC
if command -v nvidia-smi &>/dev/null; then
    ok "NVIDIA driver detectado: $(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)"
else
    warn "nvidia-smi no encontrado — se usará libx264 (CPU) como encoder"
    warn "Para NVENC instala los drivers NVIDIA manualmente"
fi

# ---------------------------------------------------------------------------
# 3. Directorios y permisos
# ---------------------------------------------------------------------------
echo ""
echo "→ Creando estructura de directorios ..."

mkdir -p "$INSTALL_DIR"/{config,logs,hls,panel,scripts}
chmod +x "$INSTALL_DIR"/scripts/*.sh

# Config por defecto si no existe
if [[ ! -f "$INSTALL_DIR/config/hls.json" ]]; then
    if [[ -f "$INSTALL_DIR/config/hls.json.example" ]]; then
        cp "$INSTALL_DIR/config/hls.json.example" "$INSTALL_DIR/config/hls.json"
        warn "Creado hls.json desde ejemplo — configura la IP y parámetros desde el panel"
    fi
fi

ok "Directorios listos"

# ---------------------------------------------------------------------------
# 4. Detectar interfaz de red principal
# ---------------------------------------------------------------------------
IFACE=$(ip route | awk '/default/{print $5; exit}')
IP=$(ip -4 addr show "$IFACE" 2>/dev/null | grep -oP '(?<=inet\s)\d+(\.\d+){3}' | head -1 || echo "127.0.0.1")

# ---------------------------------------------------------------------------
# 5. Montar tmpfs para segmentos HLS (RAM)
# ---------------------------------------------------------------------------
echo ""
echo "→ Configurando tmpfs para segmentos HLS ..."

if mount | grep -q "$INSTALL_DIR/hls"; then
    ok "tmpfs ya montado en $INSTALL_DIR/hls"
else
    mount -t tmpfs -o size=512m,mode=0755 tmpfs "$INSTALL_DIR/hls" && ok "tmpfs montado (512 MB en RAM)"
fi

# Persistir en fstab si no está ya
if ! grep -q "$INSTALL_DIR/hls" /etc/fstab; then
    echo "tmpfs  $INSTALL_DIR/hls  tmpfs  defaults,size=512m,mode=0755  0 0" >> /etc/fstab
    ok "tmpfs añadido a /etc/fstab (persistente tras reboot)"
fi

# ---------------------------------------------------------------------------
# 6. Servicio: hls-orange-tuning (red + buffers)
# ---------------------------------------------------------------------------
echo ""
echo "→ Instalando servicio hls-orange-tuning ..."

# Detectar interfaz de red del multicast (la que tiene más tráfico o la principal)
MCAST_IFACE="${MCAST_IFACE:-$IFACE}"

cat > /etc/systemd/system/hls-orange-tuning.service << EOF
[Unit]
Description=HLS Orange — network tuning + tmpfs
Before=hls-orange.service
After=network.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/bash -c '\
  for q in /sys/class/net/${MCAST_IFACE}/queues/rx-*/rps_cpus; do [ -f "\$q" ] && echo fffff > "\$q"; done; \
  for q in /sys/class/net/${MCAST_IFACE}/queues/rx-*/rps_flow_cnt; do [ -f "\$q" ] && echo 4096 > "\$q"; done; \
  sysctl -qw net.core.rmem_max=33554432 net.core.rmem_default=33554432 net.core.netdev_max_backlog=5000; \
  mount | grep -q "${INSTALL_DIR}/hls" || mount -t tmpfs -o size=512m,mode=0755 tmpfs ${INSTALL_DIR}/hls; \
  mkdir -p ${INSTALL_DIR}/hls/live'

[Install]
WantedBy=multi-user.target
EOF
ok "hls-orange-tuning.service instalado"

# ---------------------------------------------------------------------------
# 7. Servicio: hls-orange-panel (panel web)
# ---------------------------------------------------------------------------
echo "→ Instalando servicio hls-orange-panel ..."

PYTHON=$(command -v python3)

cat > /etc/systemd/system/hls-orange-panel.service << EOF
[Unit]
Description=HLS Orange — Panel de configuracion web
After=network.target

[Service]
Type=simple
User=root
Environment=PANEL_PORT=${PANEL_PORT}
ExecStart=${PYTHON} ${INSTALL_DIR}/panel/server.py
Restart=always
RestartSec=2
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
ok "hls-orange-panel.service instalado"

# ---------------------------------------------------------------------------
# 8. Servicio: hls-orange (streamer)
#    La configuración real la genera el panel al pulsar "Instalar servicio"
#    Este es un stub inicial para que el servicio exista en systemd
# ---------------------------------------------------------------------------
echo "→ Instalando servicio hls-orange ..."

NGINX=$(command -v nginx)
BASH=$(command -v bash)

cat > /etc/systemd/system/hls-orange.service << EOF
[Unit]
Description=HLS Orange — Multicast to HLS streamer
After=network.target hls-orange-tuning.service
Requires=hls-orange-tuning.service

[Service]
Type=simple
User=root
Environment=MULTICAST_URL=udp://239.0.0.1:1234
Environment=MULTICAST_IFACE=${MCAST_IFACE}
Environment=STREAM_NAME=live
Environment=ABR_PROFILE=1080p
Environment=ENCODER=nvenc
Environment=NVENC_PRESET=p4
Environment=NVENC_RC=cbr
Environment=NVENC_LOOKAHEAD=0
Environment=NVENC_AQ=0
Environment=NVENC_GPU=0
Environment=PIX_FMT=yuv420p
Environment=COLOR_RANGE=tv
Environment=COLOR_SPACE=bt709
Environment=BIT_1080P=5500
Environment=BIT_720P=3500
Environment=BIT_480P=2000
Environment=BIT_360P=500
Environment=SEGMENT_TIME=4
Environment=SEGMENT_LIST_SIZE=5
Environment=SOURCE_FPS=25
Environment=GOP_SIZE=0
Environment=MASTER_BANDWIDTH=5728000
Environment=MASTER_AVG_BANDWIDTH=5328000
Environment=MASTER_CODECS=avc1.640028,mp4a.40.2
Environment=MASTER_FRAMERATE=25.000
Environment=AUDIO_STREAM=0:1
Environment=AUDIO_RATE=48000
Environment=ABIT_1080P=192
Environment=ABIT_720P=128
Environment=ABIT_480P=96
Environment=ABIT_360P=64
Environment=RESTART_DELAY=3
Nice=5
ExecStartPre=${NGINX} -c ${INSTALL_DIR}/config/nginx.conf -t
ExecStartPre=${BASH} -c '${NGINX} -c ${INSTALL_DIR}/config/nginx.conf || true'
ExecStart=${INSTALL_DIR}/scripts/start-hls.sh
ExecStopPost=${BASH} -c '${NGINX} -c ${INSTALL_DIR}/config/nginx.conf -s stop 2>/dev/null || true; rm -rf ${INSTALL_DIR}/hls/\${STREAM_NAME} 2>/dev/null || true'
Restart=on-failure
RestartSec=3
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
ok "hls-orange.service instalado"

# ---------------------------------------------------------------------------
# 9. Habilitar servicios
# ---------------------------------------------------------------------------
echo ""
echo "→ Habilitando servicios en el arranque ..."

systemctl daemon-reload
systemctl enable hls-orange-tuning hls-orange-panel hls-orange
ok "Servicios habilitados"

# ---------------------------------------------------------------------------
# 10. Arrancar panel
# ---------------------------------------------------------------------------
echo ""
echo "→ Arrancando panel web ..."
systemctl restart hls-orange-panel
sleep 1
systemctl is-active hls-orange-panel &>/dev/null && ok "Panel arrancado" || warn "Panel no arrancó — revisa: journalctl -u hls-orange-panel"

# ---------------------------------------------------------------------------
echo ""
echo "============================================"
echo -e "${GREEN}   Instalación completada${NC}"
echo "============================================"
echo ""
echo "  Panel de control : http://${IP}:${PANEL_PORT}"
echo "  Stream HLS       : http://${IP}:${HLS_PORT}/STREAM_NAME/master.m3u8"
echo ""
echo "  Próximos pasos:"
echo "  1. Abre el panel en http://${IP}:${PANEL_PORT}"
echo "  2. Configura la URL multicast y parámetros"
echo "  3. Pulsa 'Guardar e iniciar'"
echo ""
