# supla-device dla Linuksa (SUPLA/supla-device, extras/examples/linux).
# Domyślnie budowany jest konkretny commit z gałęzi main: wydanie v26.9.2 nie ma jeszcze
# `expiration_time_sec` dla źródła MQTT, z którego korzysta wygenerowana konfiguracja.
FROM debian:bookworm-slim AS build
ARG SUPLA_DEVICE_REF=4e7240ccceb4ec4e49cc8c455eb1f0d9166aed0f
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       git ca-certificates build-essential cmake libssl-dev libcurl4-openssl-dev libyaml-cpp-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
RUN git init -q supla-device && cd supla-device \
    && git remote add origin https://github.com/SUPLA/supla-device.git \
    && git fetch -q --depth 1 origin "${SUPLA_DEVICE_REF}" \
    && git checkout -q FETCH_HEAD
# Własne rozszerzenia (dodatkowe typy kanałów): MatterLight — ściemniacz z regulacją bieli,
# MatterMeter — jednofazowy licznik energii.
COPY extensions /src/m2s-extensions
WORKDIR /src/supla-device/extras/examples/linux/build
RUN cmake -DCMAKE_BUILD_TYPE=Release \
          "-DSUPLA_LINUX_EXTENSION_DIRS=/src/m2s-extensions/matter_light;/src/m2s-extensions/matter_meter" .. \
    && make -j"$(nproc)" \
    && ./supla-device-linux --version

FROM debian:bookworm-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates libssl3 libcurl4 libyaml-cpp0.7 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 1000 --create-home supla \
    && mkdir -p /var/lib/supla-device /etc/supla-device \
    && chown supla /var/lib/supla-device && chmod 700 /var/lib/supla-device
COPY --from=build /src/supla-device/extras/examples/linux/build/supla-device-linux /usr/local/bin/
COPY entrypoint.sh /usr/local/bin/supla-entrypoint
RUN chmod 755 /usr/local/bin/supla-entrypoint
USER supla
WORKDIR /var/lib/supla-device
# Tryb „normal”: logi na stdout (widoczne w `docker compose logs supla`).
# Entrypoint restartuje supla-device po każdej zmianie pliku konfiguracyjnego.
ENTRYPOINT ["supla-entrypoint"]
