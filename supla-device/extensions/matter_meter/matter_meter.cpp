// SPDX-License-Identifier: GPL-2.0-or-later
//
// Rozszerzenie supla-device dla Linuksa: kanał "MatterMeter" — jednofazowy licznik energii.
// To standardowy ElectricityMeterParsed z dwiema różnicami:
//   1. kanał jest zgłaszany serwerowi jako jednofazowy (bez pustych faz 2 i 3 w aplikacji),
//   2. gdy dane są nieważne (urządzenie niedostępne, stan wygasł), kanał przechodzi
//      w offline zamiast pozostawać online bez pomiarów.
//
// Konfiguracja jak dla ElectricityMeterParsed, ale tylko z sekcją phase_1:
//   - type: MatterMeter
//     source: {type: MQTT, state_topic: "m2s/2/2/meter/state", expiration_time_sec: 120}
//     parser: {type: Json, refresh_time_ms: 1000}
//     phase_1:
//     - voltage: voltage
//     - current: current
//     - power_active: power
//     - fwd_act_energy: energy
//     frequency: frequency          # opcjonalnie

#include <supla-common/proto.h>
#include <supla/channels/channel.h>
#include <supla/log_wrapper.h>
#include <supla/sensor/electricity_meter_parsed.h>

#include <string>

#include "linux_channel_factory.h"
#include "linux_yaml_config.h"

namespace {

class MatterMeter : public Supla::Sensor::ElectricityMeterParsed {
 public:
  explicit MatterMeter(Supla::Parser::Parser *parser)
      : Supla::Sensor::ElectricityMeterParsed(parser) {
    extChannel.setFlag(SUPLA_CHANNEL_FLAG_PHASE2_UNSUPPORTED);
    extChannel.setFlag(SUPLA_CHANNEL_FLAG_PHASE3_UNSUPPORTED);
  }

  void readValuesFromDevice() override {
    Supla::Sensor::ElectricityMeterParsed::readValuesFromDevice();
    // Klasa bazowa przy ważnych danych sama ustawia kanał online; przy nieważnych tylko
    // zeruje pomiary. Brak danych z licznika ma być widoczny jako offline.
    if (isDataErrorLogged) {
      if (auto channel = getChannel()) {
        channel->setStateOffline();
      }
    }
  }
};

bool AddMatterMeter(const Supla::Linux::ChannelFactoryContext &context) {
  const auto &ch = context.channel;
  auto &config = context.config;
  auto parser = context.parser;

  if (parser == nullptr) {
    SUPLA_LOG_ERROR(
        "Channel[%d] config: MatterMeter requires source and parser",
        context.channelNumber);
    return false;
  }
  if (parser->isBasedOnIndex()) {
    SUPLA_LOG_ERROR("Channel[%d] config: MatterMeter requires the Json parser",
                    context.channelNumber);
    return false;
  }
  for (const char *unsupported : {"phase_2", "phase_3"}) {
    if (config.getAndMarkChannelParameter(ch, unsupported)) {
      SUPLA_LOG_ERROR("Channel[%d] config: MatterMeter is single-phase, "
                      "\"%s\" is not allowed",
                      context.channelNumber,
                      unsupported);
      return false;
    }
  }

  SUPLA_LOG_INFO(
      "Channel[%d] config: adding MatterMeter", context.channelNumber);
  auto meter = new MatterMeter(parser);

  if (auto frequencyParameter =
          config.getAndMarkChannelParameter(ch, Supla::Parser::Frequency)) {
    meter->setMapping(Supla::Parser::Frequency,
                      frequencyParameter.as<std::string>());
  }

  int mapped = 0;
  if (auto phaseParameter = config.getAndMarkChannelParameter(ch, "phase_1")) {
    for (auto param : phaseParameter) {
      for (const char *name : {"voltage",
                               "current",
                               "fwd_act_energy",
                               "rvr_act_energy",
                               "power_active",
                               "rvr_power_active",
                               "power_reactive",
                               "power_apparent",
                               "phase_angle",
                               "power_factor"}) {
        if (param[name]) {
          meter->setMapping(std::string{name} + "_1",
                            param[name].as<std::string>());
          mapped++;
        }
      }
    }
  }
  if (mapped == 0) {
    SUPLA_LOG_ERROR(
        "Channel[%d] config: MatterMeter requires phase_1 measurements",
        context.channelNumber);
    return false;
  }

  return config.addCommonChannelParameters(ch, meter);
}

}  // namespace

namespace Supla {
namespace Linux {

void initMatterMeterExtension() {
  ChannelFactoryRegistry::instance().registerFactory("matter_meter",
                                                     "MatterMeter",
                                                     AddMatterMeter);
}

}  // namespace Linux
}  // namespace Supla
