"""Wiersz poleceń: uruchomienie łącznika oraz narzędzia do parowania i konfiguracji."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import logging
import os
import re
import signal
import sys
from typing import Any

from pathlib import Path

from .config import Config, ConfigError, MatterConfig, load_config
from .matter_client import MatterClient, MatterConnectionError, MatterError
from .supla_config import ChannelConflictError, write_supla_config

DEFAULT_CONFIG = "/config/devices.yaml"
DEFAULT_SUPLA_OUTPUT = "/config/supla/supla-device.yaml"

_ONOFF_PATH = re.compile(r"^(\d+)/6/0$")


def _matter_url(args: argparse.Namespace) -> str:
    if args.matter_url:
        return args.matter_url
    try:
        return load_config(args.config).matter.url
    except ConfigError:
        return MatterConfig.url


def _load_or_default(path: str) -> Config:
    """Brak pliku nie jest błędem: przy automatycznych kanałach łącznik utworzy go sam."""
    if not Path(path).exists():
        logging.getLogger(__name__).info("brak %s — start z ustawieniami domyślnymi", path)
        return Config()
    return load_config(path)


async def _run(config: Config, config_path: str) -> None:
    from .manager import Manager
    from .runner import run
    from .web import check_web_security

    password = os.environ.get("M2S_WEB_PASSWORD") or None
    check_web_security(config.web, password)
    manager = Manager(
        config,
        config_path=Path(config_path),
        supla_output=Path(os.environ.get("M2S_SUPLA_OUTPUT", DEFAULT_SUPLA_OUTPUT)),
        supla_server=os.environ.get("SUPLA_SERVER", ""),
        supla_mail=os.environ.get("SUPLA_MAIL", ""),
    )
    task = asyncio.ensure_future(run(manager, password))
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, task.cancel)
    try:
        await task
    except asyncio.CancelledError:
        logging.getLogger(__name__).info("zatrzymano")


def _describe_node(node: dict[str, Any]) -> str:
    attrs = node.get("attributes") or {}
    endpoints = sorted(int(m.group(1)) for path in attrs if (m := _ONOFF_PATH.match(path)))
    return (
        f"node_id={node.get('node_id')}  dostępny={'tak' if node.get('available') else 'nie'}  "
        f"VID={attrs.get('0/40/2')} PID={attrs.get('0/40/4')}  "
        f"{attrs.get('0/40/1', '?')} / {attrs.get('0/40/3', '?')}  "
        f"endpointy OnOff: {endpoints or 'brak'}"
    )


async def _nodes(url: str) -> None:
    async with MatterClient(url) as client:
        nodes = await client.command("get_nodes") or []
        info = client.server_info
        print(
            f"matter-server {info.get('sdk_version')}  schemat {info.get('schema_version')}  "
            f"dataset Thread ustawiony: {info.get('thread_credentials_set')}  "
            f"Bluetooth: {info.get('bluetooth_enabled')}"
        )
        if not nodes:
            print("Brak sparowanych węzłów.")
        for node in nodes:
            print(_describe_node(node))


async def _diagnostics(url: str, node_id: int, full: bool) -> None:
    import json

    from .diagnostics import build_diagnostics

    async with MatterClient(url) as client:
        node = await client.command("get_node", node_id=node_id)
    print(json.dumps(build_diagnostics(node_id, node or {}, full), indent=2, ensure_ascii=False))


async def _set_thread_dataset(url: str, dataset: str) -> None:
    async with MatterClient(url) as client:
        await client.command("set_thread_dataset", dataset=dataset)
    print("Dataset Thread zapisany w matter-server.")


async def _commission(url: str, code: str, network_only: bool) -> None:
    # Parowanie przez Thread trwa długo — osobny, dłuższy limit czasu.
    async with MatterClient(url, command_timeout=300) as client:
        args: dict[str, Any] = {"code": code}
        if network_only:
            args["network_only"] = True
        node = await client.command("commission_with_code", **args)
    if isinstance(node, dict):
        print("Sparowano: " + _describe_node(node))
    else:
        print("Sparowano.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="m2s-bridge", description=__doc__)
    parser.add_argument(
        "--config",
        default=os.environ.get("M2S_CONFIG", DEFAULT_CONFIG),
        help=f"plik mapowania urządzeń (domyślnie {DEFAULT_CONFIG} lub $M2S_CONFIG)",
    )
    parser.add_argument(
        "--matter-url",
        default=os.environ.get("M2S_MATTER_URL"),
        help="adres WebSocket matter-server (domyślnie z pliku konfiguracyjnego)",
    )
    parser.add_argument("--log-level", default=os.environ.get("M2S_LOG_LEVEL", "INFO"))
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("run", help="uruchom łącznik (domyślne)")

    gen = sub.add_parser("gen-supla-config", help="wygeneruj supla-device.yaml z devices.yaml")
    gen.add_argument("--output", default=DEFAULT_SUPLA_OUTPUT)
    gen.add_argument("--server", default=os.environ.get("SUPLA_SERVER"), help="lub $SUPLA_SERVER")
    gen.add_argument("--mail", default=os.environ.get("SUPLA_MAIL"), help="lub $SUPLA_MAIL")
    gen.add_argument(
        "--force",
        action="store_true",
        help="pomiń kontrolę trwałości kanałów (tylko po usunięciu urządzenia z SUPLA Cloud)",
    )

    sub.add_parser("nodes", help="wypisz węzły sparowane w matter-server")

    diag = sub.add_parser("diagnostics", help="wypisz zrzut diagnostyczny węzła (JSON)")
    diag.add_argument("node_id", type=int)
    diag.add_argument("--full", action="store_true", help="bez usuwania numeru seryjnego i danych sieci")

    dataset = sub.add_parser("set-thread-dataset", help="przekaż dataset Thread do matter-server")
    dataset.add_argument("dataset", nargs="?", help="hex z `ot-ctl dataset active -x`; bez argumentu — pytanie")

    commission = sub.add_parser("commission", help="sparuj urządzenie kodem (MT:… albo numerycznym)")
    commission.add_argument("code", nargs="?", help="bez argumentu — pytanie (kod nie trafi do historii powłoki)")
    commission.add_argument(
        "--network-only",
        action="store_true",
        help="bez Bluetootha: urządzenie jest już w sieci (udostępnione z innej aplikacji)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    command = args.command or "run"
    try:
        if command == "run":
            asyncio.run(_run(_load_or_default(args.config), args.config))
        elif command == "gen-supla-config":
            server, mail = args.server or "", args.mail or ""
            account = Path(args.output).parent / "account.json"
            if (not server or not mail) and account.exists():
                # konto wpisane w panelu WWW
                import json

                saved = json.loads(account.read_text(encoding="utf-8"))
                server, mail = server or str(saved.get("server", "")), mail or str(saved.get("mail", ""))
            path = write_supla_config(load_config(args.config), args.output, server, mail, args.force)
            print(f"Zapisano {path}")
        elif command == "nodes":
            asyncio.run(_nodes(_matter_url(args)))
        elif command == "diagnostics":
            asyncio.run(_diagnostics(_matter_url(args), args.node_id, args.full))
        elif command == "set-thread-dataset":
            value = args.dataset or getpass.getpass("Dataset Thread (hex): ")
            asyncio.run(_set_thread_dataset(_matter_url(args), value.strip()))
        elif command == "commission":
            code = args.code or getpass.getpass("Kod parowania: ")
            asyncio.run(_commission(_matter_url(args), code.strip(), args.network_only))
    except (ConfigError, ChannelConflictError, ValueError) as err:
        print(f"Błąd konfiguracji: {err}", file=sys.stderr)
        return 2
    except (MatterConnectionError, MatterError) as err:
        print(f"Błąd: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
