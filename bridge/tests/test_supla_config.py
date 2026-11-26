# SPDX-FileCopyrightText: 2026 Zjawa.IT - Jarosław Zjawiński <kontakt@zjawa.it>
# SPDX-License-Identifier: GPL-2.0-or-later

import json

import pytest
import yaml

from m2s_bridge.config import parse_config
from m2s_bridge.supla_config import (
    LOCK_FILE_NAME,
    ChannelConflictError,
    build_supla_config,
    write_supla_config,
)


def cfg(*devices, **extra):
    return parse_config({"devices": list(devices), **extra})


def dev(name, channel, node=None, **extra):
    return {
        "name": name,
        "node_id": node or channel + 1,
        "endpoint": 1,
        "type": "onoff",
        "supla_channel": channel,
        **extra,
    }


def test_channel_matches_bridge_topics():
    doc = build_supla_config(cfg(dev("plug", 0, caption="Gniazdko")), "svr.example.org", "user@example.org")
    assert doc["supla"] == {"server": "svr.example.org", "mail": "user@example.org"}
    assert doc["mqtt"]["use_ssl"] is False
    assert doc["state_files_path"] == "/var/lib/supla-device"
    channel = doc["channels"][0]
    assert channel["type"] == "CustomRelay"
    assert channel["channel_number"] == 0
    assert channel["initial_caption"] == "Gniazdko"
    assert channel["state"] == "state"
    assert channel["offline_on_invalid_state"] is True
    assert channel["source"] == {
        "type": "MQTT",
        "state_topic": "m2s/1/1/onoff/state",
        "expiration_time_sec": 120,
    }
    assert channel["output"]["control_topic"] == "m2s/1/1/onoff/set"
    assert (channel["turn_on_payload"], channel["turn_off_payload"]) == ("ON", "OFF")


def test_channels_sorted_by_number_regardless_of_file_order():
    doc = build_supla_config(cfg(dev("b", 1), dev("a", 0)), "s", "m")
    assert [c["name"] for c in doc["channels"]] == ["a", "b"]


def test_expiration_can_be_disabled():
    doc = build_supla_config(cfg(dev("a", 0), state_expiration_sec=0), "s", "m")
    assert "expiration_time_sec" not in doc["channels"][0]["source"]


def test_gap_in_channel_numbers_rejected():
    with pytest.raises(ChannelConflictError):
        build_supla_config(cfg(dev("a", 0), dev("c", 2)), "s", "m")


def test_server_and_mail_required():
    with pytest.raises(ValueError):
        build_supla_config(cfg(dev("a", 0)), "", "")


def test_written_yaml_keeps_payloads_as_strings(tmp_path):
    out = write_supla_config(cfg(dev("a", 0)), tmp_path / "supla" / "supla-device.yaml", "s", "m")
    channel = yaml.safe_load(out.read_text(encoding="utf-8"))["channels"][0]
    assert channel["turn_on_payload"] == "ON" and channel["turn_off_payload"] == "OFF"
    lock = json.loads((out.parent / LOCK_FILE_NAME).read_text(encoding="utf-8"))
    assert lock == {"channels": {"0": {"type": "CustomRelay", "name": "a"}}}


def test_appending_and_renaming_is_allowed(tmp_path):
    out = tmp_path / "supla-device.yaml"
    write_supla_config(cfg(dev("a", 0)), out, "s", "m")
    write_supla_config(cfg(dev("a_nowa_nazwa", 0, node=5), dev("b", 1)), out, "s", "m")
    assert len(yaml.safe_load(out.read_text(encoding="utf-8"))["channels"]) == 2


def test_removing_channel_is_refused_and_leaves_files_untouched(tmp_path):
    out = tmp_path / "supla-device.yaml"
    write_supla_config(cfg(dev("a", 0), dev("b", 1)), out, "s", "m")
    before = out.read_text(encoding="utf-8")
    with pytest.raises(ChannelConflictError, match="usunięty"):
        write_supla_config(cfg(dev("a", 0)), out, "s", "m")
    assert out.read_text(encoding="utf-8") == before
    write_supla_config(cfg(dev("a", 0)), out, "s", "m", force=True)
    assert len(yaml.safe_load(out.read_text(encoding="utf-8"))["channels"]) == 1


def test_changed_channel_type_is_refused(tmp_path):
    out = tmp_path / "supla-device.yaml"
    write_supla_config(cfg(dev("a", 0)), out, "s", "m")
    (tmp_path / LOCK_FILE_NAME).write_text(
        json.dumps({"channels": {"0": {"type": "ThermometerParsed", "name": "a"}}}), encoding="utf-8"
    )
    with pytest.raises(ChannelConflictError, match="zmienił typ"):
        write_supla_config(cfg(dev("a", 0)), out, "s", "m")
