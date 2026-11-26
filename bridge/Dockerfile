# SPDX-FileCopyrightText: 2026 Zjawa.IT - Jarosław Zjawiński <kontakt@zjawa.it>
# SPDX-License-Identifier: GPL-2.0-or-later

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install . && useradd --uid 1000 --create-home m2s
USER m2s
ENV M2S_CONFIG=/config/devices.yaml
# Wersja i commit wbudowane w obraz (ustawia je pipeline; budowa lokalna: `dev`). Na końcu pliku,
# żeby zmiana numeru nie unieważniała wcześniejszych warstw.
ARG M2S_BUILD_VERSION=dev
ARG M2S_BUILD_REVISION=
ENV M2S_BUILD_VERSION=${M2S_BUILD_VERSION} M2S_BUILD_REVISION=${M2S_BUILD_REVISION}
ENTRYPOINT ["m2s-bridge"]
CMD ["run"]
