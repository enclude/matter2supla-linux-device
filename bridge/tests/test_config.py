# SPDX-FileCopyrightText: 2026 Zjawa.IT - Jarosław Zjawiński <kontakt@zjawa.it>
# SPDX-License-Identifier: GPL-2.0-or-later

import pytest

from m2s_bridge.config import ConfigError, config_to_raw, load_config, parse_config

DEVICE = {"name": "plug", "node_id": 1, "endpoint": 1, "type": "onoff", "supla_channel": 0}


def test_defaults():
    config = parse_config({"devices": [DEVICE]})
    assert config.matter.url == "ws://127.0.0.1:5580/ws"
    assert config.mqtt.host == "127.0.0.1"
    assert config.mqtt.topic_prefix == "m2s"
    assert config.devices[0].node_id == 1


def test_empty_file_is_valid():
    assert parse_config(None).devices == ()


@pytest.mark.parametrize(
    "change",
    [
        {"type": "dimmer"},
        {"node_id": "1"},
        {"node_id": 0},
        {"endpoint": -1},
        {"supla_channel": 128},
        {"supla_channel": True},
        {"name": "ma spacje"},
    ],
)
def test_invalid_device(change):
    with pytest.raises(ConfigError):
        parse_config({"devices": [{**DEVICE, **change}]})


def test_missing_field():
    device = dict(DEVICE)
    del device["endpoint"]
    with pytest.raises(ConfigError, match="endpoint"):
        parse_config({"devices": [device]})


@pytest.mark.parametrize(
    "second",
    [
        {**DEVICE, "node_id": 2, "supla_channel": 1},  # ta sama nazwa
        {**DEVICE, "name": "inny", "node_id": 2},  # ten sam kanał SUPLA
        {**DEVICE, "name": "inny", "supla_channel": 1},  # ten sam węzeł/endpoint
    ],
)
def test_duplicates(second):
    with pytest.raises(ConfigError):
        parse_config({"devices": [DEVICE, second]})


def test_topic_prefix_cannot_contain_wildcards():
    with pytest.raises(ConfigError):
        parse_config({"mqtt": {"topic_prefix": "m2s/#"}})


def test_expiration_must_cover_heartbeats():
    with pytest.raises(ConfigError):
        parse_config({"heartbeat_sec": 30, "state_expiration_sec": 45})
    assert parse_config({"heartbeat_sec": 30, "state_expiration_sec": 0}).state_expiration_sec == 0


def test_load_config_errors(tmp_path):
    with pytest.raises(ConfigError):
        load_config(tmp_path / "brak.yaml")
    bad = tmp_path / "bad.yaml"
    bad.write_text("devices: [", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(bad)


def test_update_check_option():
    assert parse_config({}).update_check is True
    assert config_to_raw(parse_config({}))["update_check"] is True
    config = parse_config({"update_check": False})
    assert config.update_check is False
    assert parse_config(config_to_raw(config)).update_check is False
    with pytest.raises(ConfigError, match="update_check"):
        parse_config({"update_check": "nie"})
