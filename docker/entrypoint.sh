#!/bin/sh
# Start a virtual display (and, for login, a noVNC viewer), then hand off to
# the autohypedrop CLI with whatever arguments the container was given.
set -eu

# Running under a UID that has no home directory (AHD_UID in .env)? Chromium
# still wants somewhere writable for its caches.
if [ ! -w "${HOME:-/nonexistent}" ]; then
    export HOME=/tmp/ahd-home
    mkdir -p "$HOME"
fi

if [ ! -w /data ]; then
    echo "error: /data is not writable by UID $(id -u)." >&2
    echo "Fix with: sudo chown -R $(id -u):$(id -g) ./data   (or set AHD_UID/AHD_GID in .env)" >&2
    exit 1
fi

# The login browser sizes its window from AHD_SCREEN (no window manager here).
export AHD_SCREEN="${AHD_SCREEN:-1280x800x24}"
display_num="${DISPLAY#:}"
rm -f "/tmp/.X${display_num}-lock" "/tmp/.X11-unix/X${display_num}"
Xvfb "$DISPLAY" -screen 0 "$AHD_SCREEN" -nolisten tcp >/dev/null 2>&1 &
i=0
while [ ! -e "/tmp/.X11-unix/X${display_num}" ]; do
    i=$((i + 1))
    if [ "$i" -gt 100 ]; then
        echo "error: Xvfb did not start" >&2
        exit 1
    fi
    sleep 0.1
done

if [ "${AHD_VNC:-false}" = "true" ]; then
    # x11vnc listens only inside the container; noVNC (websockify) on 6080 is
    # the only thing published, and compose binds it to 127.0.0.1 on the host.
    if [ -n "${AHD_VNC_PASSWORD:-}" ]; then
        x11vnc -storepasswd "$AHD_VNC_PASSWORD" "$HOME/.vncpass" >/dev/null 2>&1
        vnc_auth="-rfbauth $HOME/.vncpass"
    else
        vnc_auth="-nopw"
    fi
    # shellcheck disable=SC2086
    x11vnc -display "$DISPLAY" -localhost -rfbport 5900 -forever -shared $vnc_auth \
        -quiet -bg -o /tmp/x11vnc.log >/dev/null 2>&1
    websockify --web /usr/share/novnc 6080 localhost:5900 >/tmp/novnc.log 2>&1 &
    echo "Browser login screen: http://localhost:${AHD_VNC_PORT:-6080}/vnc.html?autoconnect=1&resize=scale"
fi

exec autohypedrop "$@"
