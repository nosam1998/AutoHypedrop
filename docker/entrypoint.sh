#!/usr/bin/env bash
# Container entrypoint: drop root, start a virtual display (and VNC when asked),
# then hand off to the autohypedrop CLI.
set -euo pipefail

: "${AHD_DATA_DIR:=/data}"
: "${AHD_PROFILE_DIR:=$AHD_DATA_DIR/profile}"

if [[ "$(id -u)" == 0 ]]; then
  # Run as the operator's uid/gid so files in the data volume stay theirs.
  PUID="${PUID:-1000}"
  PGID="${PGID:-1000}"
  mkdir -p "$AHD_DATA_DIR" "$AHD_PROFILE_DIR" /home/ahd
  chown "$PUID:$PGID" /home/ahd "$AHD_DATA_DIR" "$AHD_PROFILE_DIR"
  # Fix anything left root-owned by an older image, without touching the rest.
  find "$AHD_DATA_DIR" \( ! -user "$PUID" -o ! -group "$PGID" \) -exec chown -h "$PUID:$PGID" {} +
  exec env HOME=/home/ahd setpriv --reuid="$PUID" --regid="$PGID" --clear-groups -- "$0" "$@"
fi

start_display() {
  export DISPLAY=:99
  Xvfb "$DISPLAY" -screen 0 1280x900x24 -nolisten tcp >/dev/null 2>&1 &
  for _ in $(seq 50); do
    [[ -S /tmp/.X11-unix/X99 ]] && return 0
    sleep 0.1
  done
  echo "Xvfb did not start" >&2
  exit 1
}

if [[ "${AHD_VNC:-false}" == "true" ]]; then
  start_display
  vnc_auth=(-nopw)
  if [[ -n "${AHD_VNC_PASSWORD:-}" ]]; then
    vnc_auth=(-passwd "$AHD_VNC_PASSWORD")
  fi
  x11vnc -display "$DISPLAY" -rfbport 5900 -localhost -forever -shared -quiet "${vnc_auth[@]}" \
    >/dev/null 2>&1 &
  websockify --web /usr/share/novnc 6080 localhost:5900 >/dev/null 2>&1 &
  echo "Open http://localhost:6080/vnc.html in your browser to see the login window."
elif [[ -z "${DISPLAY:-}" && "${AHD_HEADLESS:-false}" != "true" ]]; then
  start_display
fi

exec autohypedrop "$@"
