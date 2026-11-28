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
// Drugi typ, "MatterRgbLight", to żarówka kolorowa (SUPLA_CHANNELFNC_DIMMER_CCT_AND_RGB):
// biel z regulacją temperatury albo kolor RGB. Matter ma jedną jasność i jeden tryb naraz,
// więc w danej chwili świeci albo biel, albo kolor (drugi suwak w aplikacji pokazuje 0).
//   state_topic  "S M B T R G Bl"  S: 1/0/-1, M: 1 = biel, 2 = kolor, B: jasność 1-100,
//                                  T: biel 0-100, R G Bl: kolor 0-255
//   set_topic    "M B T R G Bl"    M: 0 = zgaś, 1 = biel, 2 = kolor
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

// Żarówka kolorowa: wartości SUPLA (jasność bieli, jasność koloru, RGB, biel) <-> jeden
// tryb światła Matter. Polecenie z aplikacji zawiera pełny stan obu części; o trybie
// decyduje to, która z nich się zmieniła albo świeci.
class MatterRgbLight : public Supla::Control::LightingPwmBase {
 public:
  MatterRgbLight(std::string stateTopic,
                 std::string setTopic,
                 uint32_t expirationSec)
      : stateTopic(std::move(stateTopic)),
        setTopic(std::move(setTopic)),
        expirationMs(expirationSec * 1000) {
    channel.setFuncList(SUPLA_RGBW_BIT_FUNC_DIMMER |
                        SUPLA_RGBW_BIT_FUNC_RGB_LIGHTING |
                        SUPLA_RGBW_BIT_FUNC_DIMMER_AND_RGB_LIGHTING |
                        SUPLA_RGBW_BIT_FUNC_DIMMER_CCT |
                        SUPLA_RGBW_BIT_FUNC_DIMMER_CCT_AND_RGB);
    channel.setDefault(SUPLA_CHANNELFNC_DIMMER_CCT_AND_RGB);
    channel.setStateOffline();
  }

  void setRGBCCTValueOnDevice(uint32_t[5], int) override {
  }

  int32_t handleNewValueFromServer(TSD_SuplaChannelNewValue *newValue) override {
    // Stan sprzed polecenia pozwala ustalić, która część (biel czy kolor) została zmieniona.
    before = requested;
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
    SUPLA_LOG_INFO("MatterRgbLight[%d] %s",
                   getChannelNumber(),
                   online ? "online" : "offline");
  }

  // Tryb po poleceniu: 0 = zgaś, 1 = biel, 2 = kolor.
  int commandedMode() const {
    bool white = requested.whiteBrightness > 0;
    bool color = requested.colorBrightness > 0;
    if (!white && !color) {
      return 0;
    }
    if (white && color) {
      // Obie części włączone: wygrywa ta, którą zmieniono; przy remisie - dotychczasowy tryb.
      bool colorChanged = requested.colorBrightness != before.colorBrightness ||
                          requested.red != before.red ||
                          requested.green != before.green ||
                          requested.blue != before.blue;
      bool whiteChanged = requested.whiteBrightness != before.whiteBrightness ||
                          requested.whiteTemperature != before.whiteTemperature;
      if (colorChanged != whiteChanged) {
        return colorChanged ? 2 : 1;
      }
      return deviceMode == 2 ? 2 : 1;
    }
    return color ? 2 : 1;
  }

  void publishCommand(uint32_t now) {
    if (!commandPending) {
      return;
    }
    commandPending = false;
    auto &client = Supla::LinuxMqttClient::getInstance();
    if (!client || !isOnline) {
      SUPLA_LOG_WARNING("MatterRgbLight[%d] command dropped: light unavailable",
                        getChannelNumber());
      return;
    }
    int mode = commandedMode();
    int brightness = mode == 2 ? requested.colorBrightness
                               : requested.whiteBrightness;
    char payload[40] = {};
    snprintf(payload,
             sizeof(payload),
             "%d %d %d %d %d %d",
             mode,
             brightness,
             requested.whiteTemperature,
             requested.red,
             requested.green,
             requested.blue);
    // Trzeci argument to flagi MQTT-C, nie numer QoS (1 = retain) - patrz MatterLight.
    auto error = client->publish(setTopic, payload, MQTT_PUBLISH_QOS_1);
    if (error != MQTT_OK) {
      SUPLA_LOG_WARNING("MatterRgbLight[%d] command \"%s\" not sent (error %d)",
                        getChannelNumber(),
                        payload,
                        static_cast<int>(error));
      return;
    }
    holdOffUntilMs = now + kHoldOffMs;
    holdOff = true;
    if (mode != 0) {
      deviceMode = mode;
    }
    SUPLA_LOG_INFO(
        "MatterRgbLight[%d] sent \"%s\"", getChannelNumber(), payload);
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
    int state = -1, mode = 0, brightness = 0, temperature = 0;
    int red = 0, green = 0, blue = 0;
    if (sscanf(message.content.c_str(),
               "%d %d %d %d %d %d %d",
               &state,
               &mode,
               &brightness,
               &temperature,
               &red,
               &green,
               &blue) != 7 ||
        state < 0 || state > 1 || mode < 1 || mode > 2 || brightness < 0 ||
        brightness > 100 || temperature < 0 || temperature > 100 || red < 0 ||
        red > 255 || green < 0 || green > 255 || blue < 0 || blue > 255) {
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
    deviceMode = mode;

    int whiteBrightness = (state == 1 && mode == 1) ? brightness : 0;
    int colorBrightness = (state == 1 && mode == 2) ? brightness : 0;
    if (whiteBrightness == requested.whiteBrightness &&
        colorBrightness == requested.colorBrightness &&
        temperature == requested.whiteTemperature &&
        red == requested.red && green == requested.green &&
        blue == requested.blue) {
      return;
    }
    // Zmiana spoza SUPLA (panel, inny kontroler): przyjmujemy stan i raportujemy go
    // serwerowi; to nie jest polecenie, więc nic nie wraca do światła.
    SUPLA_LOG_INFO(
        "MatterRgbLight[%d] device state: mode %d, brightness %d, temperature "
        "%d, color %d/%d/%d",
        getChannelNumber(),
        mode,
        brightness,
        temperature,
        red,
        green,
        blue);
    LightingPwmBase::setRGBCCT(red,
                               green,
                               blue,
                               colorBrightness,
                               whiteBrightness,
                               temperature,
                               false,
                               true);
  }

  const std::string stateTopic;
  const std::string setTopic;
  const uint32_t expirationMs;
  RequestedState before;
  uint32_t lastPollMs = 0;
  uint32_t holdOffUntilMs = 0;
  uint32_t appliedRevision = 0;
  int deviceMode = 1;
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

bool AddMatterRgbLight(const Supla::Linux::ChannelFactoryContext &context) {
  const auto &ch = context.channel;
  auto &config = context.config;

  auto stateParameter = config.getAndMarkChannelParameter(ch, "state_topic");
  auto setParameter = config.getAndMarkChannelParameter(ch, "set_topic");
  if (!stateParameter || !setParameter) {
    SUPLA_LOG_ERROR(
        "Channel[%d] config: MatterRgbLight requires state_topic and set_topic",
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
        "Channel[%d] config: MatterRgbLight requires a valid mqtt section",
        context.channelNumber);
    return false;
  }

  SUPLA_LOG_INFO(
      "Channel[%d] config: adding MatterRgbLight", context.channelNumber);
  auto light = new MatterRgbLight(stateTopic,
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
  ChannelFactoryRegistry::instance().registerFactory(
      "matter_rgb_light", "MatterRgbLight", AddMatterRgbLight);
}

}  // namespace Linux
}  // namespace Supla
