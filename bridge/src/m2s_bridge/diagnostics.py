# SPDX-FileCopyrightText: 2026 Zjawa.IT - Jarosław Zjawiński <kontakt@zjawa.it>
# SPDX-License-Identifier: GPL-2.0-or-later

"""Zrzut diagnostyczny węzła Matter — materiał do dopisania obsługi nowego typu urządzenia.

Format jest zgodny z komendą `import_test_node` matter-server, więc zrzut można wczytać
jako węzeł testowy i rozwijać obsługę bez fizycznego urządzenia.
"""

from __future__ import annotations

from typing import Any

from . import __version__

# Klastry z danymi sieci i poświadczeniami — niepotrzebne do dopisania obsługi urządzenia.
REDACTED_CLUSTERS = {
    31: "AccessControl",
    49: "NetworkCommissioning",
    51: "GeneralDiagnostics",
    53: "ThreadNetworkDiagnostics",
    54: "WiFiNetworkDiagnostics",
    55: "EthernetNetworkDiagnostics",
    62: "OperationalCredentials",
    63: "GroupKeyManagement",
}
# Pojedyncze atrybuty identyfikujące egzemplarz lub użytkownika (BasicInformation).
REDACTED_ATTRIBUTES = {
    "0/40/5": "NodeLabel",
    "0/40/6": "Location",
    "0/40/15": "SerialNumber",
    "0/40/18": "UniqueID",
}


def _cluster(path: str) -> int | None:
    parts = path.split("/")
    try:
        return int(parts[1])
    except (IndexError, ValueError):
        return None


def build_diagnostics(node_id: int, node: dict[str, Any], full: bool = False) -> dict[str, Any]:
    attributes = dict(node.get("attributes") or {})
    removed: list[str] = []
    if not full:
        for path in list(attributes):
            if path in REDACTED_ATTRIBUTES or _cluster(path) in REDACTED_CLUSTERS:
                del attributes[path]
                removed.append(path)
    clusters: dict[str, list[int]] = {}
    for path in attributes:
        endpoint, cluster = path.split("/")[0], _cluster(path)
        if cluster is not None and cluster not in clusters.setdefault(endpoint, []):
            clusters[endpoint].append(cluster)
    return {
        "m2s_bridge": __version__,
        "redacted": not full,
        "redacted_paths": sorted(removed),
        "summary": {
            "vendor_id": attributes.get("0/40/2"),
            "product_id": attributes.get("0/40/4"),
            "vendor": attributes.get("0/40/1"),
            "product": attributes.get("0/40/3"),
            "software_version": attributes.get("0/40/10"),
            "clusters_by_endpoint": {ep: sorted(ids) for ep, ids in sorted(clusters.items(), key=lambda kv: int(kv[0]))},
        },
        "data": {
            "node": {
                "node_id": node_id,
                "date_commissioned": node.get("date_commissioned", "1970-01-01T00:00:00.000000"),
                "last_interview": node.get("last_interview", "1970-01-01T00:00:00.000000"),
                "interview_version": node.get("interview_version", 6),
                "available": bool(node.get("available")),
                "is_bridge": bool(node.get("is_bridge")),
                "attributes": attributes,
                "attribute_subscriptions": [],
            }
        },
    }
