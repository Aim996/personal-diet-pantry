FROM node:24-bookworm

ARG OPENCLAW_VERSION=2026.7.1-2
ARG PLUGIN_ARCHIVE=personal-diet-pantry-0.8.27.tgz

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        ca-certificates \
        python3 \
        python3-pip \
        python3-venv \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m venv /opt/personal-diet-pantry-python \
    && /opt/personal-diet-pantry-python/bin/python -m pip install \
        --disable-pip-version-check \
        --no-cache-dir \
        PyYAML==6.0.3 \
        tzdata==2026.3

RUN npm install --global "openclaw@${OPENCLAW_VERSION}" \
    && npm cache clean --force

RUN groupadd --gid 12001 newcomer \
    && useradd \
        --uid 12001 \
        --gid 12001 \
        --create-home \
        --shell /bin/bash \
        newcomer \
    && install -d -o newcomer -g newcomer /opt/first-user-verification

COPY --chown=newcomer:newcomer ${PLUGIN_ARCHIVE} /opt/first-user-verification/plugin.tgz
COPY --chown=newcomer:newcomer docker/first-user/verify.sh /opt/first-user-verification/verify.sh
COPY --chown=newcomer:newcomer scripts/bridge_probe.mjs /opt/first-user-verification/bridge_probe.mjs

RUN chmod 0555 /opt/first-user-verification/verify.sh

USER newcomer
WORKDIR /home/newcomer

ENV HOME=/home/newcomer \
    OPENCLAW_STATE_DIR=/home/newcomer/.openclaw \
    PERSONAL_DIET_PANTRY_DATA_DIR=/home/newcomer/personal-diet-pantry-data \
    PYTHON=/opt/personal-diet-pantry-python/bin/python

ENTRYPOINT ["/opt/first-user-verification/verify.sh"]
