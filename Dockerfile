# syntax=docker/dockerfile:1

# Ubuntu 24.04 is Playwright's primary Linux target and ships Python 3.12.
# Installing only Chromium, rather than starting from the official Playwright
# image with its three browsers, keeps the image smaller. Builds on
# linux/amd64 and linux/arm64.
ARG BASE_IMAGE=ubuntu:24.04
FROM ${BASE_IMAGE}

ARG DEBIAN_FRONTEND=noninteractive
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    DISPLAY=:99 \
    AHD_PROFILE_DIR=/data/profile \
    AHD_DATA_DIR=/data

# Chromium runs headed under a virtual display (PRD 6.3). x11vnc + noVNC let
# the operator do the one-time Google login from a browser tab (PRD 12).
# The noVNC web client is static files, so take them out of the package
# rather than installing it with its Node.js and Tk dependencies (~125 MB).
# Unpinned apt versions: Ubuntu drops superseded versions from its archive.
# hadolint ignore=DL3003,DL3008
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates python3 python3-venv tzdata tini xvfb x11vnc websockify \
 && cd /tmp && apt-get download novnc && dpkg-deb -x novnc_*.deb novnc \
 && mv novnc/usr/share/novnc /usr/share/novnc && rm -rf novnc novnc_*.deb \
 && rm -rf /var/lib/apt/lists/* \
 && python3 -m venv "$VIRTUAL_ENV" \
 && mkdir -p /tmp/.X11-unix && chmod 1777 /tmp/.X11-unix

WORKDIR /app

# Dependencies and Chromium first, so code-only changes rebuild in seconds.
# The empty package lets pip resolve pyproject.toml before the source exists.
# --no-shell skips the separate headless build (~340 MB): runs are headed by
# default, and headless can still use full Chromium via channel="chromium".
COPY pyproject.toml README.md LICENSE ./
RUN mkdir -p src/autohypedrop && touch src/autohypedrop/__init__.py \
 && pip install hatchling . \
 && playwright install --with-deps --no-shell chromium \
 && rm -rf /var/lib/apt/lists/*

COPY src ./src
RUN pip install --no-deps --no-build-isolation --force-reinstall .

# Run as UID 1000 (the usual first user on a Linux host) instead of root. The
# base image's own `ubuntu` user already holds that UID.
COPY docker/entrypoint.sh /usr/local/bin/docker-entrypoint
RUN chmod 0755 /usr/local/bin/docker-entrypoint \
 && userdel --remove ubuntu \
 && useradd --create-home --uid 1000 --user-group ahd \
 && mkdir -p /data && chown ahd:ahd /data

USER ahd
VOLUME ["/data"]
# noVNC, only used by `login`.
EXPOSE 6080

ENTRYPOINT ["tini", "--", "docker-entrypoint"]
CMD ["daemon"]
