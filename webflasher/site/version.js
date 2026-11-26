// SPDX-FileCopyrightText: 2026 Zjawa.IT - Jarosław Zjawiński <kontakt@zjawa.it>
// SPDX-License-Identifier: GPL-2.0-or-later
// Pokazuje przy przycisku, z której wersji ESP-IDF zbudowano firmware.
fetch("firmware/manifest.json")
  .then((response) => (response.ok ? response.json() : null))
  .then((manifest) => {
    if (manifest && manifest.version) document.getElementById("fw-version").textContent = "firmware: " + manifest.version;
  })
  .catch(() => {});
