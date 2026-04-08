#!/usr/bin/env bash
# =============================================================================
# HLS desde Multicast UDP — NVENC (GTX 1050 Ti)
# =============================================================================
set -euo pipefail

# ---------------------------------------------------------------------------
# CONFIGURACIÓN (variables de entorno o valores por defecto)
# ---------------------------------------------------------------------------
MULTICAST_URL="${MULTICAST_URL:-udp://239.0.0.1:1234}"
MULTICAST_IFACE="${MULTICAST_IFACE:-}"
STREAM_NAME="${STREAM_NAME:-live}"
HLS_DIR="/opt/hls-orange/hls"
LOG_DIR="/opt/hls-orange/logs"

# HLS
SEGMENT_TIME="${SEGMENT_TIME:-4}"
PLAYLIST_SIZE="${SEGMENT_LIST_SIZE:-5}"
RESTART_DELAY="${RESTART_DELAY:-3}"

# Encoder: nvenc | libx264
ENCODER="${ENCODER:-nvenc}"

# Perfil ABR: 1080p_720p | 1080p_720p_480p | 720p_480p_360p | 1080p | 720p | 480p
ABR_PROFILE="${ABR_PROFILE:-1080p_720p}"

# NVENC — preset p1(velocidad) … p7(calidad). p4 = balance ideal para live
NVENC_PRESET="${NVENC_PRESET:-p4}"
# RC mode: cbr (live recomendado) | vbr_hq (mejor calidad, más GPU) | constqp
NVENC_RC="${NVENC_RC:-cbr}"
# Lookahead: 0=sin latencia extra, 8/16=mejor compresión (añade latencia)
NVENC_LOOKAHEAD="${NVENC_LOOKAHEAD:-0}"
# AQ espacial/temporal (solo efectivo con vbr_hq)
NVENC_AQ="${NVENC_AQ:-0}"
# GPU index (0 = primera GPU)
NVENC_GPU="${NVENC_GPU:-0}"

# Bitrates por calidad (kbps, sin la 'k')
BIT_1080P="${BIT_1080P:-4500}"
BIT_720P="${BIT_720P:-2500}"
BIT_480P="${BIT_480P:-1000}"
BIT_360P="${BIT_360P:-500}"

# CPU fallback threads (solo libx264)
THREADS_PER_ENC="${THREADS_PER_ENC:-2}"

# Pixel format y color metadata
# yuv420p     = 8 bit 4:2:0 — compatible con el 100% de dispositivos (recomendado)
# yuv420p10le = 10 bit 4:2:0 — HDR, solo dispositivos modernos con H.265
PIX_FMT="${PIX_FMT:-yuv420p}"
# Color range: tv (16-235, broadcast) | pc (0-255, full range)
COLOR_RANGE="${COLOR_RANGE:-tv}"
# Color space: bt709 (HD estándar) | bt601 (SD legacy) | bt2020nc (HDR)
COLOR_SPACE="${COLOR_SPACE:-bt709}"

# GOP (Group of Pictures)
# Regla: GOP debe ser exactamente framerate × segment_time
# Así cada segmento HLS empieza en un I-frame (obligatorio para ABR)
# Ejemplos: 25fps×4s=100 | 30fps×4s=120 | 50fps×4s=200
SOURCE_FPS="${SOURCE_FPS:-25}"
# GOP_SIZE=0 → cálculo automático (SOURCE_FPS × SEGMENT_TIME)
GOP_SIZE="${GOP_SIZE:-0}"

# ---------------------------------------------------------------------------
FFMPEG=$(command -v ffmpeg 2>/dev/null || true)
[[ -z "$FFMPEG" ]] && { echo "ERROR: ffmpeg no encontrado"; exit 1; }

# Limpiar contenido anterior antes de arrancar
rm -rf "${HLS_DIR:?}/$STREAM_NAME"
mkdir -p "$HLS_DIR/$STREAM_NAME" "$LOG_DIR"

# Master playlist (perfiles single)
MASTER_BANDWIDTH="${MASTER_BANDWIDTH:-5728000}"
MASTER_AVG_BANDWIDTH="${MASTER_AVG_BANDWIDTH:-5328000}"
MASTER_CODECS="${MASTER_CODECS:-avc1.640028,mp4a.40.2}"
MASTER_FRAMERATE="${MASTER_FRAMERATE:-25.000}"

# Audio
AUDIO_STREAM="${AUDIO_STREAM:-0:1}"
AUDIO_RATE="${AUDIO_RATE:-48000}"
ABIT_1080P="${ABIT_1080P:-192}"
ABIT_720P="${ABIT_720P:-128}"
ABIT_480P="${ABIT_480P:-96}"
ABIT_360P="${ABIT_360P:-64}"

INPUT_OPTS="-fflags +nobuffer+discardcorrupt+igndts -flags low_delay \
  -err_detect ignore_err -max_error_rate 1.0 \
  -analyzeduration 5000000 -probesize 10000000"

if [[ -n "$MULTICAST_IFACE" ]]; then
    LOCAL_IP=$(ip -4 addr show "$MULTICAST_IFACE" 2>/dev/null | grep -oP '(?<=inet\s)\d+(\.\d+){3}' | head -1 || true)
    [[ -n "$LOCAL_IP" ]] && INPUT_OPTS="$INPUT_OPTS -localaddr $LOCAL_IP"
fi

# Calcular GOP final
if [[ "$GOP_SIZE" == "0" ]]; then
    G=$(( SOURCE_FPS * SEGMENT_TIME ))
else
    G="$GOP_SIZE"
fi

echo "=============================="
echo " HLS Multicast Streamer"
echo "=============================="
echo " Fuente   : $MULTICAST_URL"
echo " Perfil   : $ABR_PROFILE"
echo " Encoder  : $ENCODER | preset=$NVENC_PRESET rc=$NVENC_RC lookahead=$NVENC_LOOKAHEAD"
echo " Bitrates : 1080p=${BIT_1080P}k 720p=${BIT_720P}k 480p=${BIT_480P}k 360p=${BIT_360P}k"
echo " HLS      : seg=${SEGMENT_TIME}s × ${PLAYLIST_SIZE} = $((SEGMENT_TIME * PLAYLIST_SIZE))s buffer"
echo " GOP      : ${G} frames (${SOURCE_FPS}fps × ${SEGMENT_TIME}s)"
echo "=============================="

# ---------------------------------------------------------------------------
# Helper: opciones de encoder para un stream dado
# Uso: enc_opts <stream_idx> <bitrate_k> <profile> <level>
# ---------------------------------------------------------------------------
enc_opts() {
    local idx="$1" bit="$2" prof="$3" lvl="$4"
    local maxrate=$(( bit * 11 / 10 ))
    local bufsize=$(( bit * 2 ))
    # G es variable global calculada antes de llamar run_ffmpeg

    if [[ "$ENCODER" == "nvenc" ]]; then
        local aq_opts=""
        if [[ "$NVENC_AQ" == "1" ]]; then
            aq_opts="-spatial_aq:v:${idx} 1 -temporal_aq:v:${idx} 1 -aq-strength:v:${idx} 8"
        fi
        echo "-c:v:${idx} h264_nvenc \
          -gpu:v:${idx} ${NVENC_GPU} \
          -preset:v:${idx} ${NVENC_PRESET} \
          -rc:v:${idx} ${NVENC_RC} \
          -b:v:${idx} ${bit}k \
          -maxrate:v:${idx} ${maxrate}k \
          -bufsize:v:${idx} ${bufsize}k \
          -profile:v:${idx} ${prof} \
          -level:v:${idx} ${lvl} \
          -rc-lookahead:v:${idx} ${NVENC_LOOKAHEAD} \
          -2pass:v:${idx} 0 \
          -g ${G} -keyint_min ${G} -sc_threshold 0 \
          -pix_fmt ${PIX_FMT} \
          -color_range ${COLOR_RANGE} \
          -colorspace ${COLOR_SPACE} \
          -color_trc ${COLOR_SPACE} \
          -color_primaries ${COLOR_SPACE} \
          ${aq_opts}"
    else
        echo "-c:v:${idx} libx264 \
          -preset:v:${idx} veryfast \
          -b:v:${idx} ${bit}k \
          -maxrate:v:${idx} ${maxrate}k \
          -bufsize:v:${idx} ${bufsize}k \
          -profile:v:${idx} ${prof} \
          -level:v:${idx} ${lvl} \
          -threads:v:${idx} ${THREADS_PER_ENC} \
          -g ${G} -keyint_min ${G} -sc_threshold 0 -pix_fmt yuv420p"
    fi
}

# Helper: audio options para un stream
aud_opts() {
    local idx="$1" bit="$2"
    echo "-c:a:${idx} aac -b:a:${idx} ${bit}k -ar:${idx} ${AUDIO_RATE} -ac:${idx} 2 -profile:a:${idx} aac_low"
}

# ---------------------------------------------------------------------------
run_ffmpeg() {
    local OUT="$HLS_DIR/$STREAM_NAME"
    # shellcheck disable=SC2086

    case "$ABR_PROFILE" in

    # ---- 2 calidades -------------------------------------------------------
    1080p_720p)
        mkdir -p "$OUT/1080p" "$OUT/720p"
        $FFMPEG -loglevel warning $INPUT_OPTS -i "$MULTICAST_URL" \
          -filter_complex \
            "[0:0]yadif=mode=0:parity=tff,split=2[v1][v2]; \
             [v1]scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2[o1]; \
             [v2]scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2[o2]" \
          -map "[o1]" -map $AUDIO_STREAM \
          -map "[o2]" -map $AUDIO_STREAM \
          $(enc_opts 0 "$BIT_1080P" main 4.0) $(aud_opts 0 "$ABIT_1080P") \
          $(enc_opts 1 "$BIT_720P"  main 3.1) $(aud_opts 1 "$ABIT_720P") \
          -var_stream_map "v:0,a:0,agroup:audio,name:1080p v:1,a:1,agroup:audio,name:720p" \
          -f hls -hls_time "$SEGMENT_TIME" -hls_list_size "$PLAYLIST_SIZE" \
          -hls_flags delete_segments+append_list+independent_segments \
          -hls_segment_type mpegts \
          -hls_segment_filename "$OUT/%v/seg_%05d.ts" \
          -master_pl_name index.m3u8 "$OUT/%v/index.m3u8" \
          2>> "$LOG_DIR/ffmpeg.log"
        ;;

    1080p_720p_480p)
        mkdir -p "$OUT/1080p" "$OUT/720p" "$OUT/480p"
        $FFMPEG -loglevel warning $INPUT_OPTS -i "$MULTICAST_URL" \
          -filter_complex \
            "[0:0]yadif=mode=0:parity=tff,split=3[v1][v2][v3]; \
             [v1]scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2[o1]; \
             [v2]scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2[o2]; \
             [v3]scale=854:480:force_original_aspect_ratio=decrease,pad=854:480:(ow-iw)/2:(oh-ih)/2[o3]" \
          -map "[o1]" -map $AUDIO_STREAM \
          -map "[o2]" -map $AUDIO_STREAM \
          -map "[o3]" -map $AUDIO_STREAM \
          $(enc_opts 0 "$BIT_1080P" main     4.0) $(aud_opts 0 "$ABIT_1080P") \
          $(enc_opts 1 "$BIT_720P"  main     3.1) $(aud_opts 1 "$ABIT_720P") \
          $(enc_opts 2 "$BIT_480P"  baseline 3.0) $(aud_opts 2 "$ABIT_480P") \
          -var_stream_map "v:0,a:0,agroup:audio,name:1080p v:1,a:1,agroup:audio,name:720p v:2,a:2,agroup:audio,name:480p" \
          -f hls -hls_time "$SEGMENT_TIME" -hls_list_size "$PLAYLIST_SIZE" \
          -hls_flags delete_segments+append_list+independent_segments \
          -hls_segment_type mpegts \
          -hls_segment_filename "$OUT/%v/seg_%05d.ts" \
          -master_pl_name index.m3u8 "$OUT/%v/index.m3u8" \
          2>> "$LOG_DIR/ffmpeg.log"
        ;;

    720p_480p_360p)
        mkdir -p "$OUT/720p" "$OUT/480p" "$OUT/360p"
        $FFMPEG -loglevel warning $INPUT_OPTS -i "$MULTICAST_URL" \
          -filter_complex \
            "[0:0]yadif=mode=0:parity=tff,split=3[v1][v2][v3]; \
             [v1]scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2[o1]; \
             [v2]scale=854:480:force_original_aspect_ratio=decrease,pad=854:480:(ow-iw)/2:(oh-ih)/2[o2]; \
             [v3]scale=640:360:force_original_aspect_ratio=decrease,pad=640:360:(ow-iw)/2:(oh-ih)/2[o3]" \
          -map "[o1]" -map $AUDIO_STREAM \
          -map "[o2]" -map $AUDIO_STREAM \
          -map "[o3]" -map $AUDIO_STREAM \
          $(enc_opts 0 "$BIT_720P"  main     3.1) $(aud_opts 0 "$ABIT_720P") \
          $(enc_opts 1 "$BIT_480P"  baseline 3.0) $(aud_opts 1 "$ABIT_480P") \
          $(enc_opts 2 "$BIT_360P"  baseline 3.0) $(aud_opts 2 "$ABIT_360P") \
          -var_stream_map "v:0,a:0,agroup:audio,name:720p v:1,a:1,agroup:audio,name:480p v:2,a:2,agroup:audio,name:360p" \
          -f hls -hls_time "$SEGMENT_TIME" -hls_list_size "$PLAYLIST_SIZE" \
          -hls_flags delete_segments+append_list+independent_segments \
          -hls_segment_type mpegts \
          -hls_segment_filename "$OUT/%v/seg_%05d.ts" \
          -master_pl_name index.m3u8 "$OUT/%v/index.m3u8" \
          2>> "$LOG_DIR/ffmpeg.log"
        ;;

    720p_480p)
        mkdir -p "$OUT/720p" "$OUT/480p"
        $FFMPEG -loglevel warning $INPUT_OPTS -i "$MULTICAST_URL" \
          -filter_complex \
            "[0:0]yadif=mode=0:parity=tff,split=2[v1][v2]; \
             [v1]scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2[o1]; \
             [v2]scale=854:480:force_original_aspect_ratio=decrease,pad=854:480:(ow-iw)/2:(oh-ih)/2[o2]" \
          -map "[o1]" -map $AUDIO_STREAM \
          -map "[o2]" -map $AUDIO_STREAM \
          $(enc_opts 0 "$BIT_720P"  main     3.1) $(aud_opts 0 "$ABIT_720P") \
          $(enc_opts 1 "$BIT_480P"  baseline 3.0) $(aud_opts 1 "$ABIT_480P") \
          -var_stream_map "v:0,a:0,agroup:audio,name:720p v:1,a:1,agroup:audio,name:480p" \
          -f hls -hls_time "$SEGMENT_TIME" -hls_list_size "$PLAYLIST_SIZE" \
          -hls_flags delete_segments+append_list+independent_segments \
          -hls_segment_type mpegts \
          -hls_segment_filename "$OUT/%v/seg_%05d.ts" \
          -master_pl_name index.m3u8 "$OUT/%v/index.m3u8" \
          2>> "$LOG_DIR/ffmpeg.log"
        ;;

    # ---- Single bitrate ----------------------------------------------------
    1080p)
        mkdir -p "$OUT"
        printf '#EXTM3U\n#EXT-X-VERSION:3\n\n#EXT-X-STREAM-INF:BANDWIDTH=%s,AVERAGE-BANDWIDTH=%s,CODECS="%s",RESOLUTION=1920x1080,FRAME-RATE=%s\nindex.m3u8\n' \
            "$MASTER_BANDWIDTH" "$MASTER_AVG_BANDWIDTH" "$MASTER_CODECS" "$MASTER_FRAMERATE" \
            > "$OUT/master.m3u8"
        $FFMPEG -loglevel warning $INPUT_OPTS -i "$MULTICAST_URL" \
          -filter_complex "[0:0]yadif=mode=0:parity=tff[v]" \
          -map "[v]" -map $AUDIO_STREAM \
          $(enc_opts 0 "$BIT_1080P" main 4.0) $(aud_opts 0 "$ABIT_1080P") \
          -f hls -hls_time "$SEGMENT_TIME" -hls_list_size "$PLAYLIST_SIZE" \
          -hls_flags delete_segments+append_list \
          -hls_segment_type mpegts \
          -hls_segment_filename "$OUT/seg_%05d.ts" \
          "$OUT/index.m3u8" \
          2>> "$LOG_DIR/ffmpeg.log"
        ;;

    720p)
        mkdir -p "$OUT"
        printf '#EXTM3U\n#EXT-X-VERSION:3\n\n#EXT-X-STREAM-INF:BANDWIDTH=%s,AVERAGE-BANDWIDTH=%s,CODECS="%s",RESOLUTION=1280x720,FRAME-RATE=%s\nindex.m3u8\n' \
            "$MASTER_BANDWIDTH" "$MASTER_AVG_BANDWIDTH" "$MASTER_CODECS" "$MASTER_FRAMERATE" \
            > "$OUT/master.m3u8"
        $FFMPEG -loglevel warning $INPUT_OPTS -i "$MULTICAST_URL" \
          -filter_complex "[0:0]yadif=mode=0:parity=tff[v]" \
          -map "[v]" -map $AUDIO_STREAM \
          $(enc_opts 0 "$BIT_720P" main 3.1) $(aud_opts 0 "$ABIT_720P") \
          -f hls -hls_time "$SEGMENT_TIME" -hls_list_size "$PLAYLIST_SIZE" \
          -hls_flags delete_segments+append_list \
          -hls_segment_type mpegts \
          -hls_segment_filename "$OUT/seg_%05d.ts" \
          "$OUT/index.m3u8" \
          2>> "$LOG_DIR/ffmpeg.log"
        ;;

    *)
        echo "ERROR: ABR_PROFILE desconocido: $ABR_PROFILE"
        exit 1
        ;;
    esac
}

# ---------------------------------------------------------------------------
while true; do
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Arrancando FFmpeg (perfil: $ABR_PROFILE)..."
    run_ffmpeg || true
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] FFmpeg terminó. Reiniciando en ${RESTART_DELAY}s..."
    sleep "$RESTART_DELAY"
done
