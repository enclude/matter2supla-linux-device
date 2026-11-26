# SPDX-FileCopyrightText: 2026 Zjawa.IT - Jarosław Zjawiński <kontakt@zjawa.it>
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Strona do wgrywania firmware dongla Thread (ot_rcp) z przeglądarki, przez Web Serial.
# Etap 1 buduje niezmieniony przykład `ot_rcp` z ESP-IDF dla obsługiwanych układów,
# etap 2 pobiera ESP Web Tools, etap 3 to statyczna strona w nginx.

ARG IDF_IMAGE=espressif/idf:release-v5.5

FROM ${IDF_IMAGE} AS firmware
SHELL ["/bin/bash", "-c"]
COPY build-firmware.sh /build-firmware.sh
RUN . "$IDF_PATH/export.sh" >/dev/null && bash /build-firmware.sh /out

FROM alpine:3.21 AS webtools
ARG EWT_VERSION=10.5.2
ARG EWT_SHA256=35681b4f1910a95f51a592ad9701f059ff60d2902ad94df9e1ad7ee59ab6b7ca
RUN wget -q -O /ewt.tgz "https://registry.npmjs.org/esp-web-tools/-/esp-web-tools-${EWT_VERSION}.tgz" \
 && echo "${EWT_SHA256}  /ewt.tgz" | sha256sum -c - \
 && mkdir /ewt && tar -xzf /ewt.tgz -C /ewt --strip-components=1

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY site/ /usr/share/nginx/html/
COPY --from=webtools /ewt/dist/web/ /usr/share/nginx/html/ewt/
COPY --from=webtools /ewt/LICENSE /usr/share/nginx/html/ewt/LICENSE
COPY --from=firmware /out/ /usr/share/nginx/html/firmware/
