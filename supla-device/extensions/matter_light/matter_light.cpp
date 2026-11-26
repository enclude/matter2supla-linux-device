// SPDX-FileCopyrightText: 2026 Zjawa.IT - Jarosław Zjawiński <kontakt@zjawa.it>
// SPDX-License-Identifier: GPL-2.0-or-later
//
// Rozszerzenie supla-device dla Linuksa: kanał "MatterLight" — ściemniacz z regulacją
// temperatury bieli (SUPLA_CHANNELFNC_DIMMER_CCT) sterujący światłem Matter przez łącznik
// m2s-bridge. Klasa bazowa LightingPwmBase obsługuje całą stronę SUPLA (wartości z aplikacji,
// raportowanie stanu, pamięć stanu); w wersji dla Linuksa brakuje tylko przekazania wartości
// do fizycznego światła i odbioru jego stanu — to robi ten plik, przez broker MQTT.
//
// Tematy (zwykły tekst, liczby rozdzielone spacją; skala SUPLA):
//   state_topic  "S B T"  S: 1 = świeci, 0 = zgaszone, -1 = niedostępne
//                         B: jasność 1-100, T: biel 0 (ciepła) - 100 (zimna)
//   set_topic    "B T"    B: 0 = zgaś, 1-100 = jasność; T: 0-100
//
// Konfiguracja kanału w supla-device.yaml:
//   - type: MatterLight
//     state_topic: "m2s/3/1/light_cct/state"
//     set_topic: "m2s/3/1/light_cct/set"
//     expiration_time_sec: 120      # opcjonalnie; 0 = stan nie wygasa

#include <supla-common/proto.h>
#include <supla/channels/channel.h>
#include <supla/control/lighting_pwm_base.h>
#include <supla/log_wrapper.h>
#include <supla/time.h>

#include <cstdint>
#include <cstdio>
#include <exception>
#include <memory>
#include <string>
#include <utility>
#include <vector>

#include "linux_channel_factory.h"
#include "linux_mqtt_client.h"
#include "linux_yaml_config.h"

namespace {

constexpr uint32_t kPollMs = 100;
// Po poleceniu z SUPLA światło przez chwilę zgłasza wartości pośrednie (płynne przejście).
// W tym czasie nie odsyłamy ich do serwera, żeby suwak w aplikacji nie skakał.
constexpr uint32_t kHoldOffMs = 3000;

class MatterLight : public Supla::Control::LightingPwmBase {
 public:
  MatterLight(std::string stateTopic,
              std::string setTopic,
              uint32_t expirationSec)
      : stateTopic(std::move(stateTopic)),
        setTopic(std::move(setTopic)),
        expirationMs(expirationSec * 1000) {
    channel.setFuncList(SUPLA_RGBW_BIT_FUNC_DIMMER |
                        SUPLA_RGBW_BIT_FUNC_DIMMER_CCT);
    channel.setDefault(SUPLA_CHANNELFNC_DIMMER_CCT);
    channel.setStateOffline();
  }

  // Wyjścia PWM nie istnieją — światłem steruje urządzenie Matter.
  void setRGBCCTValueOnDevice(uint32_t[5], int) override {
  }

  // Do światła trafiają wyłącznie polecenia z serwera SUPLA. Wewnętrzne wywołania setRGBCCT
  // (odtworzenie stanu przy starcie, zmiana funkcji kanału, przyjęcie stanu z urządzenia)
  // nie mogą przełączać fizycznego światła.
  int32_t handleNewValueFromServer(TSD_SuplaChannelNewValue *newValue) override {
    int32_t result = LightingPwmBase::handleNewValueFromServer(newValue);
    commandPending = true;
    return result;
  }

  void iterateAlways() override {
    LightingPwmBase::iterateAlways();
    uint32_t now = millis();
    if (now - lastPollMs < kPollMs) {
      return;
    }
    lastPollMs = now;
    publishCommand(now);
    readDeviceState(now);
  }

 private:
  void setOnline(bool online) {
    if (online == isOnline && onlineKnown) {
      return;
    }
    onlineKnown = true;
    isOnline = online;
    if (online) {
      channel.setStateOnline();
    } else {
      channel.setStateOffline();
    }
    SUPLA_LOG_INFO("MatterLight[%d] %s",
                   getChannelNumber(),
                   online ? "online" : "offline");
  }

  void publishCommand(uint32_t now) {
    if (!commandPending) {
      return;
    }
    commandPending = false;
    auto &client = Supla::LinuxMqttClient::getInstance();
    if (!client || !isOnline) {
      SUPLA_LOG_WARNING("MatterLight[%d] command dropped: light unavailable",
                        getChannelNumber());
      return;
    }
    char payload[24] = {};
    snprintf(payload,
             sizeof(payload),
             "%d %d",
             requested.whiteBrightness,
             requested.whiteTemperature);
    // Trzeci argument to flagi biblioteki MQTT-C, nie numer QoS: wartość 1 oznaczałaby
    // "retain", a zachowane polecenie wróciłoby do światła po restarcie łącznika.
    auto error = client->publish(setTopic, payload, MQTT_PUBLISH_QOS_1);
    if (error != MQTT_OK) {
      SUPLA_LOG_WARNING("MatterLight[%d] command \"%s\" not sent (error %d)",
                        getChannelNumber(),
                        payload,
                        static_cast<int>(error));
      return;
    }
    holdOffUntilMs = now + kHoldOffMs;
    holdOff = true;
    SUPLA_LOG_INFO(
        "MatterLight[%d] sent \"%s\"", getChannelNumber(), payload);
  }

  void readDeviceState(uint32_t now) {
    auto &client = Supla::LinuxMqttClient::getInstance();
    if (!client || !client->isConnected()) {
      setOnline(false);
      return;
    }
    auto messages = client->getMessages({stateTopic});
    if (messages.empty() || !messages[0].received) {
      setOnline(false);
      return;
    }
    const auto &message = messages[0];
    if (expirationMs != 0 && now - message.updatedMs > expirationMs) {
      setOnline(false);
      return;
    }
    int state = -1;
    int brightness = 0;
    int temperature = 0;
    if (sscanf(message.content.c_str(),
               "%d %d %d",
               &state,
               &brightness,
               &temperature) != 3 ||
        state < 0 || state > 1 || brightness < 0 || brightness > 100 ||
        temperature < 0 || temperature > 100) {
      setOnline(false);
      return;
    }
    setOnline(true);

    if (holdOff) {
      if (static_cast<int32_t>(now - holdOffUntilMs) < 0) {
        return;
      }
      holdOff = false;
    } else if (message.revision == appliedRevision) {
      return;
    }
    appliedRevision = message.revision;

    int whiteBrightness = state == 1 ? brightness : 0;
    if (whiteBrightness == requested.whiteBrightness &&
        temperature == requested.whiteTemperature) {
      return;
    }
    // Stan zmieniony poza SUPLA (panel, inny kontroler) — przyjmujemy go i raportujemy
    // serwerowi; nie jest to polecenie, więc nic nie wraca do światła.
    SUPLA_LOG_INFO("MatterLight[%d] device state: brightness %d, temperature %d",
                   getChannelNumber(),
                   whiteBrightness,
                   temperature);
    LightingPwmBase::setRGBCCT(
        -1, -1, -1, -1, whiteBrightness, temperature, false, true);
  }

  const std::string stateTopic;
  const std::string setTopic;
  const uint32_t expirationMs;
  uint32_t lastPollMs = 0;
  uint32_t holdOffUntilMs = 0;
  uint32_t appliedRevision = 0;
  bool commandPending = false;
  bool holdOff = false;
  bool isOnline = false;
  bool onlineKnown = false;
};

bool AddMatterLight(const Supla::Linux::ChannelFactoryContext &context) {
  const auto &ch = context.channel;
  auto &config = context.config;

  auto stateParameter = config.getAndMarkChannelParameter(ch, "state_topic");
  auto setParameter = config.getAndMarkChannelParameter(ch, "set_topic");
  if (!stateParameter || !setParameter) {
    SUPLA_LOG_ERROR(
        "Channel[%d] config: MatterLight requires state_topic and set_topic",
        context.channelNumber);
    return false;
  }
  int expirationSec = 0;
  if (auto expirationParameter =
          config.getAndMarkChannelParameter(ch, "expiration_time_sec")) {
    expirationSec = expirationParameter.as<int>();
    if (expirationSec < 0 || expirationSec > 86400) {
      SUPLA_LOG_ERROR(
          "Channel[%d] config: expiration_time_sec has to be in range 0..86400",
          context.channelNumber);
      return false;
    }
  }

  auto stateTopic = stateParameter.as<std::string>();
  try {
    Supla::LinuxMqttClient::getInstance(config)->subscribeTopic(stateTopic);
  } catch (const std::exception &) {
    SUPLA_LOG_ERROR(
        "Channel[%d] config: MatterLight requires a valid mqtt section",
        context.channelNumber);
    return false;
  }

  SUPLA_LOG_INFO(
      "Channel[%d] config: adding MatterLight", context.channelNumber);
  auto light = new MatterLight(stateTopic,
                               setParameter.as<std::string>(),
                               static_cast<uint32_t>(expirationSec));
  return config.addCommonChannelParameters(ch, light);
}

}  // namespace

namespace Supla {
namespace Linux {

void initMatterLightExtension() {
  ChannelFactoryRegistry::instance().registerFactory("matter_light",
                                                     "MatterLight",
                                                     AddMatterLight);
}

}  // namespace Linux
}  // namespace Supla
