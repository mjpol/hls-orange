#!/usr/bin/env python3
"""
HLS Orange — Panel de configuración (stdlib pura, sin dependencias)
"""
import http.server, json, os, subprocess, sys, urllib.parse
from pathlib import Path

BASE_DIR     = Path("/opt/hls-orange")
CONFIG_FILE  = BASE_DIR / "config" / "hls.json"
SERVICE_NAME = "hls-orange"
PANEL_PORT   = int(os.environ.get("PANEL_PORT", "9090"))

DEFAULT_CONFIG = {
    # Entrada
    "multicast_url":   "udp://239.0.0.1:1234",
    "multicast_iface": "eth0",
    "stream_name":     "live",
    # Perfil
    "abr_profile":     "1080p_720p",
    # NVENC
    "encoder":         "nvenc",
    "nvenc_preset":    "p4",
    "nvenc_rc":        "cbr",
    "nvenc_lookahead": "0",
    "nvenc_aq":        "0",
    "nvenc_gpu":       "0",
    # Pixel format y color
    "pix_fmt":         "yuv420p",
    "color_range":     "tv",
    "color_space":     "bt709",
    # Bitrates (kbps)
    "bit_1080p":       "4500",
    "bit_720p":        "2500",
    "bit_480p":        "1000",
    "bit_360p":        "500",
    # Master playlist (perfiles single)
    "master_bandwidth":     "5728000",
    "master_avg_bandwidth": "5328000",
    "master_codecs":        "avc1.640028,mp4a.40.2",
    "master_framerate":     "25.000",
    # Audio
    "audio_stream":    "0:1",
    "audio_rate":      "48000",
    "abit_1080p":      "192",
    "abit_720p":       "128",
    "abit_480p":       "96",
    "abit_360p":       "64",
    # HLS
    "segment_time":    "4",
    "playlist_size":   "5",
    "source_fps":      "25",
    "gop_size":        "0",
    "restart_delay":   "3",
}

def load_config():
    if CONFIG_FILE.exists():
        try:
            d = json.loads(CONFIG_FILE.read_text())
            # rellenar campos nuevos que no existieran antes
            for k, v in DEFAULT_CONFIG.items():
                d.setdefault(k, v)
            return d
        except Exception:
            pass
    return dict(DEFAULT_CONFIG)

def save_config(data):
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(data, indent=2))

# ---------------------------------------------------------------------------
def svc(action):
    try:
        r = subprocess.run(["systemctl", action, SERVICE_NAME],
                           capture_output=True, text=True, timeout=10)
        return r.returncode == 0, (r.stdout + r.stderr).strip() or f"OK"
    except Exception as e:
        return False, str(e)

def svc_status():
    try:
        r = subprocess.run(["systemctl", "is-active", SERVICE_NAME],
                           capture_output=True, text=True, timeout=5)
        active = r.stdout.strip()
        r2 = subprocess.run(
            ["systemctl", "show", SERVICE_NAME,
             "--property=ActiveState,SubState,MainPID,ExecMainStartTimestamp"],
            capture_output=True, text=True, timeout=5)
        props = dict(l.split("=",1) for l in r2.stdout.strip().splitlines() if "=" in l)
        running   = active == "active"
        pid       = props.get("MainPID","0")
        since_raw = props.get("ExecMainStartTimestamp","—")
        try:
            # systemd format: "Wed 2026-04-08 15:08:55 CEST"
            parts = since_raw.split()
            d = parts[1].split("-")   # ['2026','04','08']
            t = parts[2]              # '15:08:55'
            since = f"{d[2]}/{d[1]}/{d[0]} {t}"
        except Exception:
            since = since_raw
        installed = Path(f"/etc/systemd/system/{SERVICE_NAME}.service").exists()
        return {"active": active, "running": running,
                "installed": installed,
                "pid":   pid if (running and pid != "0") else "—",
                "since": since if running else "—"}
    except Exception as e:
        return {"active":"error","running":False,"pid":"—","since":str(e)}

def connection_count():
    try:
        r = subprocess.run(["ss","-tn","sport","=",":8008"],
                           capture_output=True, text=True, timeout=3)
        return sum(1 for l in r.stdout.splitlines() if "ESTAB" in l)
    except Exception:
        return 0

def gpu_info():
    try:
        r = subprocess.run(
            ["nvidia-smi","--query-gpu=name,utilization.encoder,memory.used,memory.total,temperature.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            parts = [x.strip() for x in r.stdout.strip().split(",")]
            return {"name":parts[0],"enc":parts[1]+"%","mem_used":parts[2]+"MiB",
                    "mem_total":parts[3]+"MiB","temp":parts[4]+"°C","ok":True}
    except Exception:
        pass
    return {"ok":False}

def get_ffmpeg_cmd():
    try:
        r = subprocess.run(["pgrep", "-x", "ffmpeg"], capture_output=True, text=True)
        pid = r.stdout.strip().splitlines()[0] if r.stdout.strip() else None
        if not pid:
            return None
        with open(f"/proc/{pid}/cmdline") as f:
            parts = f.read().split("\x00")
        return " ".join(p for p in parts if p)
    except Exception:
        return None

def get_logs(n=80):
    try:
        r = subprocess.run(["journalctl","-u",SERVICE_NAME,"-n",str(n),
                            "--no-pager","--output=short"],
                           capture_output=True, text=True, timeout=10)
        return r.stdout or "(sin logs)"
    except Exception:
        f = BASE_DIR/"logs"/"ffmpeg.log"
        return "\n".join(f.read_text().splitlines()[-n:]) if f.exists() else "(sin logs)"

def install_service(cfg):
    nginx  = subprocess.run(["which","nginx"], capture_output=True,text=True).stdout.strip() or "/usr/sbin/nginx"
    bash   = subprocess.run(["which","bash"],  capture_output=True,text=True).stdout.strip() or "/bin/bash"
    profile_labels = {
        "1080p_720p":       "1080p + 720p",
        "1080p_720p_480p":  "1080p + 720p + 480p",
        "720p_480p_360p":   "720p + 480p + 360p",
        "720p_480p":        "720p + 480p",
        "1080p":            "Single 1080p",
        "720p":             "Single 720p",
    }
    label = profile_labels.get(cfg["abr_profile"], cfg["abr_profile"])
    unit = f"""[Unit]
Description=HLS Orange — {label} ({cfg['encoder'].upper()})
After=network.target hls-orange-tuning.service
Requires=hls-orange-tuning.service

[Service]
Type=simple
User=root
Environment=MULTICAST_URL={cfg['multicast_url']}
Environment=MULTICAST_IFACE={cfg['multicast_iface']}
Environment=STREAM_NAME={cfg['stream_name']}
Environment=ABR_PROFILE={cfg['abr_profile']}
Environment=ENCODER={cfg['encoder']}
Environment=NVENC_PRESET={cfg['nvenc_preset']}
Environment=NVENC_RC={cfg['nvenc_rc']}
Environment=NVENC_LOOKAHEAD={cfg['nvenc_lookahead']}
Environment=NVENC_AQ={cfg['nvenc_aq']}
Environment=NVENC_GPU={cfg['nvenc_gpu']}
Environment=PIX_FMT={cfg.get('pix_fmt','yuv420p')}
Environment=COLOR_RANGE={cfg.get('color_range','tv')}
Environment=COLOR_SPACE={cfg.get('color_space','bt709')}
Environment=BIT_1080P={cfg['bit_1080p']}
Environment=BIT_720P={cfg['bit_720p']}
Environment=BIT_480P={cfg['bit_480p']}
Environment=BIT_360P={cfg['bit_360p']}
Environment=SEGMENT_TIME={cfg['segment_time']}
Environment=SEGMENT_LIST_SIZE={cfg['playlist_size']}
Environment=SOURCE_FPS={cfg.get('source_fps','25')}
Environment=GOP_SIZE={cfg.get('gop_size','0')}
Environment=MASTER_BANDWIDTH={cfg.get('master_bandwidth','5728000')}
Environment=MASTER_AVG_BANDWIDTH={cfg.get('master_avg_bandwidth','5328000')}
Environment=MASTER_CODECS="{cfg.get('master_codecs','avc1.640028,mp4a.40.2')}"
Environment=MASTER_FRAMERATE={cfg.get('master_framerate','25.000')}
Environment=AUDIO_STREAM={cfg.get('audio_stream','0:1')}
Environment=AUDIO_RATE={cfg.get('audio_rate','48000')}
Environment=ABIT_1080P={cfg.get('abit_1080p','192')}
Environment=ABIT_720P={cfg.get('abit_720p','128')}
Environment=ABIT_480P={cfg.get('abit_480p','96')}
Environment=ABIT_360P={cfg.get('abit_360p','64')}
Environment=RESTART_DELAY={cfg['restart_delay']}
Nice=5
ExecStartPre={nginx} -c /opt/hls-orange/config/nginx.conf -t
ExecStartPre={bash} -c '{nginx} -c /opt/hls-orange/config/nginx.conf || true'
ExecStart=/opt/hls-orange/scripts/start-hls.sh
ExecStopPost={bash} -c '{nginx} -c /opt/hls-orange/config/nginx.conf -s stop 2>/dev/null || kill $(cat /tmp/nginx.pid 2>/dev/null) 2>/dev/null || true; rm -rf /opt/hls-orange/hls/${{STREAM_NAME}} 2>/dev/null || true'
Restart=on-failure
RestartSec={cfg['restart_delay']}
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
"""
    try:
        Path(f"/etc/systemd/system/{SERVICE_NAME}.service").write_text(unit)
        subprocess.run(["systemctl","daemon-reload"], check=True, timeout=10)
        subprocess.run(["systemctl","enable",SERVICE_NAME], check=True, timeout=10)
        return True, "Servicio instalado correctamente"
    except Exception as e:
        return False, str(e)

# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------
def sel(name, options, current):
    """options = list of (value, label)"""
    html = f'<select name="{name}">'
    for val, lbl in options:
        sel = " selected" if val == current else ""
        html += f'<option value="{val}"{sel}>{lbl}</option>'
    html += "</select>"
    return html

def render_html(cfg, status, msg="", msg_type="ok"):
    gpu = gpu_info()
    mc  = "#27ae60" if msg_type == "ok" else "#e74c3c"
    msg_html = f'<div class="msg" style="background:{mc}">{msg}</div>' if msg else ""
    rc  = "badge-on" if status["running"] else "badge-off"
    rt  = "ACTIVO" if status["running"] else "DETENIDO"

    gpu_block = ""
    if gpu["ok"]:
        gpu_block = f"""
        <div class="stat-row"><span class="stat-label">GPU</span><span>{gpu['name']}</span></div>
        <div class="stat-row"><span class="stat-label">Encoder uso</span><span>{gpu['enc']}</span></div>
        <div class="stat-row"><span class="stat-label">VRAM</span><span>{gpu['mem_used']} / {gpu['mem_total']}</span></div>
        <div class="stat-row"><span class="stat-label">Temp</span><span>{gpu['temp']}</span></div>"""

    # URLs según perfil
    sn = cfg["stream_name"]
    single_profiles = ("1080p","720p")
    if cfg["abr_profile"] in single_profiles:
        urls_html = f'<div class="pill-url">http://109.232.71.120:8008/{sn}/master.m3u8 &nbsp;<span style="color:#888">← master</span></div>'
        urls_html += f'<div class="pill-url" style="margin-top:.3rem">http://109.232.71.120:8008/{sn}/index.m3u8 &nbsp;<span style="color:#888">← media</span></div>'
    else:
        resols = cfg["abr_profile"].split("_")
        urls_html  = f'<div class="pill-url">http://109.232.71.120:8008/{sn}/index.m3u8 &nbsp;<span style="color:#888">← ABR</span></div>'
        for r in resols:
            urls_html += f'<div class="pill-url" style="margin-top:.3rem">http://109.232.71.120:8008/{sn}/{r}/index.m3u8</div>'

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Panel de control</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#0d0d1a;color:#e0e0e0;font-family:'Segoe UI',sans-serif}}
header{{background:#12122a;padding:.8rem 1.5rem;display:flex;align-items:center;gap:1.2rem;border-bottom:2px solid #e50914;flex-wrap:wrap}}
header h1{{font-size:1.3rem;color:#fff;flex:1}}
.hdr-info{{display:flex;align-items:center;gap:1.2rem;font-size:.82rem}}
.hdr-item{{color:#9090b0}}.hdr-item span{{color:#e0e0e0;margin-left:.3rem}}
.badge-on{{background:#27ae60;color:#fff;padding:.2rem .7rem;border-radius:20px;font-size:.8rem;font-weight:bold}}
.badge-off{{background:#c0392b;color:#fff;padding:.2rem .7rem;border-radius:20px;font-size:.8rem;font-weight:bold}}
.container{{max-width:1100px;margin:0 auto;padding:1.2rem}}
.grid3{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:1.2rem}}
.grid2{{display:grid;grid-template-columns:1fr 1fr;gap:1.2rem}}
.full{{grid-column:1/-1}}
@media(max-width:800px){{.grid3,.grid2{{grid-template-columns:1fr}}}}
.card{{background:#1a1a2e;border-radius:10px;padding:1.3rem;border:1px solid #2a2a45}}
.card h2{{font-size:.78rem;color:#7a7aaa;margin-bottom:1rem;text-transform:uppercase;letter-spacing:.08em;border-bottom:1px solid #2a2a45;padding-bottom:.5rem}}
label{{display:block;font-size:.82rem;color:#9090b0;margin:.75rem 0 .2rem}}
label:first-of-type{{margin-top:0}}
input,select{{width:100%;padding:.5rem .7rem;background:#0d0d1a;border:1px solid #3a3a55;color:#eee;border-radius:6px;font-size:.9rem}}
input:focus,select:focus{{outline:none;border-color:#e50914}}
.btn{{display:inline-block;padding:.55rem 1.1rem;border:none;border-radius:6px;cursor:pointer;font-size:.85rem;font-weight:600;transition:opacity .15s}}
.btn:hover{{opacity:.82}}
.btn-red  {{background:#e50914;color:#fff}}
.btn-green{{background:#27ae60;color:#fff}}
.btn-amber{{background:#f39c12;color:#fff}}
.btn-gray {{background:#444466;color:#fff}}
.btn-dark {{background:#c0392b;color:#fff}}
.actions{{display:flex;flex-wrap:wrap;gap:.5rem;margin-top:1rem}}
.msg{{padding:.65rem 1rem;border-radius:6px;margin-bottom:1rem;font-size:.88rem}}
.stat-row{{display:flex;justify-content:space-between;padding:.35rem 0;border-bottom:1px solid #1e1e35;font-size:.85rem}}
.stat-row:last-child{{border:none}}
.stat-label{{color:#6a6a9a}}
pre{{background:#0a0a18;border:1px solid #2a2a45;border-radius:6px;padding:.9rem;
     font-size:.75rem;overflow-y:auto;max-height:260px;white-space:pre-wrap;word-break:break-all;color:#9090c0}}
.pill-url{{background:#0a0a18;border:1px solid #2a2a45;border-radius:5px;
           padding:.4rem .7rem;font-family:monospace;font-size:.78rem;color:#7ec8e3;
           word-break:break-all;margin-top:.3rem}}
.row2{{display:grid;grid-template-columns:1fr 1fr;gap:.7rem}}
.hint{{font-size:.75rem;color:#555588;margin-top:.2rem}}
</style>
</head>
<body>
<header>
  <h1>Panel de control</h1>
  <form method="GET" action="/" style="margin:0">
    <button class="btn btn-gray" style="font-size:.8rem;padding:.35rem .8rem">&#8635; Actualizar</button>
  </form>
  <div class="hdr-info" id="hdr-live">
    <span id="svc-badge" class="{rc}">{rt}</span>
    <span id="hdr-pid" class="hdr-item" {"style='display:none'" if not status['running'] else ""}>PID<span>{status['pid']}</span></span>
    <span id="hdr-since" class="hdr-item" {"style='display:none'" if not status['running'] else ""}>Desde<span>{status['since']}</span></span>
    {"<span id='hdr-gpu' class='hdr-item'>GPU<span>" + gpu['name'] + "</span></span>" if gpu["ok"] else "<span id='hdr-gpu' class='hdr-item' style='display:none'></span>"}
    {"<span id='hdr-enc' class='hdr-item'>Enc<span>" + gpu['enc'] + "</span></span>" if gpu["ok"] else "<span id='hdr-enc' class='hdr-item' style='display:none'></span>"}
    {"<span id='hdr-vram' class='hdr-item'>VRAM<span>" + gpu['mem_used'] + "/" + gpu['mem_total'] + "</span></span>" if gpu["ok"] else "<span id='hdr-vram' class='hdr-item' style='display:none'></span>"}
    {"<span id='hdr-temp' class='hdr-item'>Temp<span>" + gpu['temp'] + "</span></span>" if gpu["ok"] else "<span id='hdr-temp' class='hdr-item' style='display:none'></span>"}
    <span id="hdr-conn" class="hdr-item">Conexiones<span>{connection_count()}</span></span>
  </div>
</header>
<div class="container">
{msg_html}
<form method="POST" action="/save">
<div class="grid3">

  <!-- ===== ENTRADA ===== -->
  <div class="card">
    <h2>&#128225; Entrada multicast</h2>
    <label>URL fuente</label>
    <input name="multicast_url" value="{cfg['multicast_url']}" required placeholder="udp://239.x.x.x:puerto">
    <p class="hint">Formatos: udp:// &nbsp;|&nbsp; rtp:// &nbsp;|&nbsp; srt://</p>
    <label>Interfaz de red</label>
    <input name="multicast_iface" value="{cfg['multicast_iface']}" placeholder="eth0 / enp1s0f3">
    <label>Nombre del stream</label>
    <input name="stream_name" value="{cfg['stream_name']}" pattern="[a-zA-Z0-9_-]+" required>

    <h2 style="margin-top:1.2rem">&#127925; Audio</h2>
    <label>Stream de audio</label>
    {sel("audio_stream",[
      ("0:1",   "0:1 — Audio por defecto ✓"),
      ("0:a:0", "0:a:0 — Primer stream de audio"),
      ("0:a:1", "0:a:1 — Segundo stream de audio"),
      ("0:a:2", "0:a:2 — Tercer stream de audio"),
    ], cfg.get("audio_stream","0:1"))}

    <label>Sample rate</label>
    {sel("audio_rate",[
      ("48000","48000 Hz — Estándar broadcast ✓"),
      ("44100","44100 Hz — CD / streaming web"),
    ], cfg.get("audio_rate","48000"))}

    <label>Bitrates audio (kbps)</label>
    <div class="row2">
      <div><label>1080p</label><input name="abit_1080p" type="number" min="32" max="320" value="{cfg.get('abit_1080p','192')}"></div>
      <div><label>720p</label> <input name="abit_720p"  type="number" min="32" max="320" value="{cfg.get('abit_720p','128')}"></div>
      <div><label>480p</label> <input name="abit_480p"  type="number" min="32" max="192" value="{cfg.get('abit_480p','96')}"></div>
      <div><label>360p</label> <input name="abit_360p"  type="number" min="32" max="128" value="{cfg.get('abit_360p','64')}"></div>
    </div>

    <h2 style="margin-top:1.2rem">&#128279; URLs</h2>
    {urls_html}

    <h2 style="margin-top:1.3rem">&#9654; Control del servicio</h2>
    <div class="actions">
      {"" if status.get("installed") else '<button class="btn btn-gray" type="submit" formaction="/service/install">&#9881; Instalar servicio</button>'}
      <button class="btn btn-red" type="submit">&#128190; Guardar</button>
      {"" if status["running"] else '<button class="btn btn-green" type="submit" formaction="/service/start">&#9654; Iniciar</button>'}
      {"" if status["running"] else '<button class="btn btn-green" type="submit" formaction="/service/save_start">&#9654; Guardar e iniciar</button>'}
      {"" if not status["running"] else '<button class="btn btn-amber" type="submit" formaction="/service/save_restart">&#8635; Guardar y reiniciar</button>'}
      {"" if not status["running"] else '<button class="btn btn-dark" type="submit" formaction="/service/stop">&#9632; Detener</button>'}
    </div>
  </div>

  <!-- ===== PERFIL ABR ===== -->
  <div class="card">
    <h2>&#127916; Perfil de calidad</h2>
    <label>Perfil ABR</label>
    {sel("abr_profile",[
      ("1080p_720p",      "1080p + 720p  (recomendado)"),
      ("1080p_720p_480p", "1080p + 720p + 480p"),
      ("720p_480p_360p",  "720p + 480p + 360p"),
      ("720p_480p",       "720p + 480p"),
      ("1080p",           "Single — 1080p"),
      ("720p",            "Single — 720p"),
    ], cfg["abr_profile"])}
    <p class="hint">El dispositivo elige automáticamente la calidad según su ancho de banda</p>

    <h2 style="margin-top:1.2rem">&#127911; Bitrates (kbps)</h2>
    <div class="row2">
      <div><label>1080p</label><input name="bit_1080p" type="number" min="500" max="20000" value="{cfg['bit_1080p']}"></div>
      <div><label>720p</label><input name="bit_720p"  type="number" min="300" max="10000" value="{cfg['bit_720p']}"></div>
      <div><label>480p</label><input name="bit_480p"  type="number" min="200" max="5000"  value="{cfg['bit_480p']}"></div>
      <div><label>360p</label><input name="bit_360p"  type="number" min="100" max="2000"  value="{cfg['bit_360p']}"></div>
    </div>

    <h2 style="margin-top:1.2rem">&#128196; Master playlist (perfil single)</h2>
    <p class="hint" style="margin-bottom:.5rem">Solo se aplica en perfiles single 1080p / 720p</p>
    <div class="row2">
      <div><label>BANDWIDTH (bps)</label><input name="master_bandwidth" type="number" min="100000" max="50000000" value="{cfg.get('master_bandwidth','5728000')}"></div>
      <div><label>AV-BANDWIDTH (bps)</label><input name="master_avg_bandwidth" type="number" min="100000" max="50000000" value="{cfg.get('master_avg_bandwidth','5328000')}"></div>
    </div>
    <label>CODECS</label>
    {sel("master_codecs",[
      ("avc1.640028,mp4a.40.2", "avc1.640028,mp4a.40.2 — H.264 High L4.0 + AAC-LC ✓"),
      ("avc1.4d4028,mp4a.40.2", "avc1.4d4028,mp4a.40.2 — H.264 Main L4.0 + AAC-LC"),
      ("avc1.42c028,mp4a.40.2", "avc1.42c028,mp4a.40.2 — H.264 Baseline L4.0 + AAC-LC"),
    ], cfg.get("master_codecs","avc1.640028,mp4a.40.2"))}
    <label>FRAME-RATE</label>
    {sel("master_framerate",[
      ("25.000","25.000 — PAL / IPTV Europa ✓"),
      ("50.000","50.000 — PAL HD 50fps"),
      ("29.970","29.970 — NTSC"),
      ("59.940","59.940 — NTSC HD"),
    ], cfg.get("master_framerate","25.000"))}

    <h2 style="margin-top:1.2rem">&#9881; Configuración HLS</h2>
    <label>Duración de segmento</label>
    {sel("segment_time",[
      ("2","2s — Baja latencia (~6s buffer)"),
      ("4","4s — Recomendado (~20s buffer) ✓"),
      ("6","6s — Alta estabilidad (~30s buffer)"),
    ], cfg["segment_time"])}

    <label>Segmentos en playlist</label>
    {sel("playlist_size",[
      ("3","3 segmentos"),
      ("5","5 segmentos ✓"),
      ("8","8 segmentos"),
    ], cfg["playlist_size"])}

    <label>FPS de la fuente</label>
    {sel("source_fps",[
      ("25","25 fps — PAL / IPTV Europa ✓"),
      ("30","30 fps — NTSC / streams americanos"),
      ("50","50 fps — PAL HD (50i desentrelazado)"),
      ("60","60 fps — NTSC HD"),
    ], cfg.get("source_fps","25"))}

    <label>GOP size (frames)</label>
    {sel("gop_size",[
      ("0",  "Auto — FPS × duración segmento ✓"),
      ("50", "50  — 2s@25fps / zapping rápido"),
      ("100","100 — 4s@25fps (recomendado)"),
      ("125","125 — 5s@25fps"),
      ("150","150 — 6s@25fps / mayor compresión"),
      ("120","120 — 4s@30fps"),
      ("240","240 — 4s@60fps"),
    ], cfg.get("gop_size","0"))}
    <p class="hint">GOP debe ser múltiplo exacto del segmento — de lo contrario ABR falla en cambio de calidad</p>

    <label>Reintentar tras fallo (seg)</label>
    <input name="restart_delay" type="number" min="1" max="60" value="{cfg['restart_delay']}">
  </div>

  <!-- ===== NVENC ===== -->
  <div class="card">
    <h2>&#9889; Encoder NVENC (GTX 1050 Ti)</h2>
    <label>Encoder</label>
    {sel("encoder",[("nvenc","h264_nvenc — GPU (recomendado)"),("libx264","libx264 — CPU (fallback)")], cfg["encoder"])}

    <label>Preset NVENC</label>
    {sel("nvenc_preset",[
      ("p1","p1 — Máxima velocidad (menor calidad)"),
      ("p2","p2 — Muy rápido"),
      ("p3","p3 — Rápido"),
      ("p4","p4 — Equilibrio calidad/velocidad ✓"),
      ("p5","p5 — Lento (mejor calidad)"),
      ("p6","p6 — Más lento"),
      ("p7","p7 — Máxima calidad (más GPU)"),
    ], cfg["nvenc_preset"])}

    <label>Modo de control de tasa (RC)</label>
    {sel("nvenc_rc",[
      ("cbr",    "CBR — Bitrate constante (live streaming ✓)"),
      ("vbr_hq", "VBR HQ — Calidad variable (mejor imagen)"),
      ("constqp","ConstQP — Calidad fija (sin control de bitrate)"),
    ], cfg["nvenc_rc"])}

    <label>Lookahead (frames)</label>
    {sel("nvenc_lookahead",[
      ("0", "0 — Desactivado (mínima latencia) ✓"),
      ("8", "8 — Bajo (mejor compresión)"),
      ("16","16 — Medio"),
      ("32","32 — Alto (máxima compresión)"),
    ], cfg["nvenc_lookahead"])}

    <label>Adaptive Quantization (AQ)</label>
    {sel("nvenc_aq",[
      ("0","Desactivado ✓"),
      ("1","Activado — spatial+temporal AQ (solo VBR HQ)"),
    ], cfg["nvenc_aq"])}

    <label>GPU index</label>
    {sel("nvenc_gpu",[("0","GPU 0 (GTX 1050 Ti)"),("1","GPU 1")], cfg["nvenc_gpu"])}

    <h2 style="margin-top:1.2rem">&#127912; Pixel format y color</h2>
    <label>Pixel format</label>
    {sel("pix_fmt",[
      ("yuv420p",    "yuv420p — 8 bit 4:2:0 (100% compatible) ✓"),
      ("yuv420p10le","yuv420p10le — 10 bit 4:2:0 (HDR, solo dispositivos modernos)"),
    ], cfg.get("pix_fmt","yuv420p"))}

    <label>Color range</label>
    {sel("color_range",[
      ("tv","tv — Limited 16-235 (broadcast / IPTV) ✓"),
      ("pc","pc — Full 0-255 (streaming web / PC)"),
    ], cfg.get("color_range","tv"))}

    <label>Color space</label>
    {sel("color_space",[
      ("bt709", "BT.709 — HD estándar (1080p / 720p) ✓"),
      ("bt601", "BT.601 — SD legacy (contenido antiguo)"),
      ("bt2020nc","BT.2020 — HDR/WCG (solo con 10 bit)"),
    ], cfg.get("color_space","bt709"))}
  </div>


</div><!-- grid3 -->
</form>

<!-- FFMPEG CMD -->
{(lambda cmd: f"""
<div class="card full" style="margin-top:1.2rem">
  <h2>&#9881; Comando FFmpeg activo</h2>
  <pre style="color:#7ec8e3">{cmd}</pre>
</div>""" if cmd else "")(get_ffmpeg_cmd())}

<!-- LOGS -->
<div class="card full" style="margin-top:1.2rem">
  <h2>&#128196; Logs
    <form method="GET" action="/" style="display:inline;margin-left:.5rem">
      <button class="btn btn-gray" style="font-size:.75rem;padding:.3rem .7rem">&#8635; Actualizar</button>
    </form>
  </h2>
  <pre>{get_logs(80)}</pre>
</div>

</div>
<script>
  var pre=document.querySelector('pre');
  if(pre) pre.scrollTop=pre.scrollHeight;

  // Polling en tiempo real del header (cada 5 segundos)
  function setSpanVal(id, label, val) {{
    var el=document.getElementById(id);
    if(!el) return;
    el.innerHTML=label+'<span>'+val+'</span>';
    el.style.display='';
  }}
  function hideSpan(id) {{ var el=document.getElementById(id); if(el) el.style.display='none'; }}

  function pollStatus() {{
    fetch('/api/status').then(function(r) {{ return r.json(); }}).then(function(d) {{
      var badge=document.getElementById('svc-badge');
      if(badge) {{
        badge.textContent=d.running?'ACTIVO':'DETENIDO';
        badge.className=d.running?'badge-on':'badge-off';
      }}
      if(d.running && d.pid && d.pid!='—') {{
        setSpanVal('hdr-pid','PID',d.pid);
        setSpanVal('hdr-since','Desde',d.since);
      }} else {{
        hideSpan('hdr-pid'); hideSpan('hdr-since');
      }}
      if(d.gpu_ok) {{
        setSpanVal('hdr-gpu','GPU',d.gpu_name);
        setSpanVal('hdr-enc','Enc',d.gpu_enc);
        setSpanVal('hdr-vram','VRAM',d.gpu_mem_used+'/'+d.gpu_mem_total);
        setSpanVal('hdr-temp','Temp',d.gpu_temp);
      }} else {{
        hideSpan('hdr-gpu'); hideSpan('hdr-enc'); hideSpan('hdr-vram'); hideSpan('hdr-temp');
      }}
      setSpanVal('hdr-conn','Conexiones',d.connections);
    }}).catch(function() {{}});
  }}
  setInterval(pollStatus, 5000);
</script>
</body>
</html>"""

# ---------------------------------------------------------------------------
class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def send_html(self, html, code=200):
        b = html.encode()
        self.send_response(code)
        self.send_header("Content-Type","text/html; charset=utf-8")
        self.send_header("Content-Length",str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def redirect_msg(self, msg, ok=True):
        """PRG: Post → Redirect → Get. Evita el 'reenviar formulario' del navegador."""
        t = "ok" if ok else "err"
        encoded = urllib.parse.quote(msg)
        self.send_response(303)
        self.send_header("Location", f"/?msg={encoded}&t={t}")
        self.end_headers()

    def send_json(self, data, code=200):
        b = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def read_form(self):
        n = int(self.headers.get("Content-Length",0))
        return dict(urllib.parse.parse_qsl(self.rfile.read(n).decode()))

    def parse_cfg(self, form):
        return {
            "multicast_url":   form.get("multicast_url",   DEFAULT_CONFIG["multicast_url"]).strip(),
            "multicast_iface": form.get("multicast_iface", DEFAULT_CONFIG["multicast_iface"]).strip(),
            "stream_name":     form.get("stream_name",     DEFAULT_CONFIG["stream_name"]).strip(),
            "abr_profile":     form.get("abr_profile",     DEFAULT_CONFIG["abr_profile"]),
            "encoder":         form.get("encoder",         DEFAULT_CONFIG["encoder"]),
            "nvenc_preset":    form.get("nvenc_preset",    DEFAULT_CONFIG["nvenc_preset"]),
            "nvenc_rc":        form.get("nvenc_rc",        DEFAULT_CONFIG["nvenc_rc"]),
            "nvenc_lookahead": form.get("nvenc_lookahead", DEFAULT_CONFIG["nvenc_lookahead"]),
            "nvenc_aq":        form.get("nvenc_aq",        DEFAULT_CONFIG["nvenc_aq"]),
            "nvenc_gpu":       form.get("nvenc_gpu",       DEFAULT_CONFIG["nvenc_gpu"]),
            "pix_fmt":         form.get("pix_fmt",         DEFAULT_CONFIG["pix_fmt"]),
            "color_range":     form.get("color_range",     DEFAULT_CONFIG["color_range"]),
            "color_space":     form.get("color_space",     DEFAULT_CONFIG["color_space"]),
            "bit_1080p":       form.get("bit_1080p",       DEFAULT_CONFIG["bit_1080p"]).strip(),
            "bit_720p":        form.get("bit_720p",        DEFAULT_CONFIG["bit_720p"]).strip(),
            "bit_480p":        form.get("bit_480p",        DEFAULT_CONFIG["bit_480p"]).strip(),
            "bit_360p":        form.get("bit_360p",        DEFAULT_CONFIG["bit_360p"]).strip(),
            "master_bandwidth":     form.get("master_bandwidth",     DEFAULT_CONFIG["master_bandwidth"]).strip(),
            "master_avg_bandwidth": form.get("master_avg_bandwidth", DEFAULT_CONFIG["master_avg_bandwidth"]).strip(),
            "master_codecs":        form.get("master_codecs",        DEFAULT_CONFIG["master_codecs"]).strip(),
            "master_framerate":     form.get("master_framerate",     DEFAULT_CONFIG["master_framerate"]),
            "audio_stream":    form.get("audio_stream",    DEFAULT_CONFIG["audio_stream"]),
            "audio_rate":      form.get("audio_rate",      DEFAULT_CONFIG["audio_rate"]),
            "abit_1080p":      form.get("abit_1080p",      DEFAULT_CONFIG["abit_1080p"]).strip(),
            "abit_720p":       form.get("abit_720p",       DEFAULT_CONFIG["abit_720p"]).strip(),
            "abit_480p":       form.get("abit_480p",       DEFAULT_CONFIG["abit_480p"]).strip(),
            "abit_360p":       form.get("abit_360p",       DEFAULT_CONFIG["abit_360p"]).strip(),
            "segment_time":    form.get("segment_time",    DEFAULT_CONFIG["segment_time"]),
            "playlist_size":   form.get("playlist_size",   DEFAULT_CONFIG["playlist_size"]),
            "source_fps":      form.get("source_fps",      DEFAULT_CONFIG["source_fps"]),
            "gop_size":        form.get("gop_size",        DEFAULT_CONFIG["gop_size"]),
            "restart_delay":   form.get("restart_delay",   DEFAULT_CONFIG["restart_delay"]).strip(),
        }

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        p      = parsed.path
        qs     = dict(urllib.parse.parse_qsl(parsed.query))
        if p in ("/", "/logs"):
            msg  = urllib.parse.unquote(qs.get("msg", ""))
            mtyp = qs.get("t", "ok")
            self.send_html(render_html(load_config(), svc_status(), msg, mtyp))
        elif p == "/api/status":
            st = svc_status()
            g  = gpu_info()
            self.send_json({**st,
                "connections":  connection_count(),
                "gpu_ok":       g["ok"],
                "gpu_name":     g.get("name",""),
                "gpu_enc":      g.get("enc",""),
                "gpu_mem_used": g.get("mem_used",""),
                "gpu_mem_total":g.get("mem_total",""),
                "gpu_temp":     g.get("temp",""),
            })
        elif p == "/api/config":
            self.send_json(load_config())
        else:
            self.send_response(404); self.end_headers()

    def do_POST(self):
        p    = self.path
        form = self.read_form()
        cfg  = load_config()

        if p == "/save":
            cfg = self.parse_cfg(form)
            save_config(cfg)
            self.redirect_msg("Configuracion guardada.")

        elif p == "/service/install":
            cfg = self.parse_cfg(form)
            save_config(cfg)
            ok, out = install_service(cfg)
            self.redirect_msg(out, ok)

        elif p == "/service/start":
            ok, out = svc("start")
            self.redirect_msg(out, ok)

        elif p == "/service/save_start":
            cfg = self.parse_cfg(form)
            save_config(cfg)
            install_service(cfg)
            ok, out = svc("start")
            self.redirect_msg(out, ok)

        elif p == "/service/save_restart":
            cfg = self.parse_cfg(form)
            save_config(cfg)
            install_service(cfg)
            ok, out = svc("restart")
            self.redirect_msg(out, ok)

        elif p == "/service/stop":
            ok, out = svc("stop")
            self.redirect_msg(out, ok)

        else:
            self.send_response(404); self.end_headers()

# ---------------------------------------------------------------------------
if __name__ == "__main__":
    server = http.server.HTTPServer(("0.0.0.0", PANEL_PORT), Handler)
    print(f"Panel HLS Orange en http://0.0.0.0:{PANEL_PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        sys.exit(0)
