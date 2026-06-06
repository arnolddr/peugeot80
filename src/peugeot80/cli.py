"""Command-line entry point."""

from __future__ import annotations

import argparse
import logging
import sys

from .config import load_config


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _cmd_run(args: argparse.Namespace) -> int:
    from .charger import build_charger
    from .controller import Controller
    from .soc import build_soc_provider

    cfg = load_config(args.config)
    _setup_logging(cfg.get_path("logging.level", "INFO"))

    soc = build_soc_provider(cfg["soc"])
    charger = build_charger(cfg["charger"])
    Controller(cfg, soc, charger).run()
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    from .charger import build_charger
    from .soc import build_soc_provider

    cfg = load_config(args.config)
    _setup_logging(cfg.get_path("logging.level", "INFO"))

    soc = build_soc_provider(cfg["soc"])
    charger = build_charger(cfg["charger"])
    try:
        reading = soc.read()
        print(f"SoC: {reading.percent:.1f}%  charging={reading.charging}")
        print(f"Charger state: {charger.state().value}")
    finally:
        soc.close()
        charger.close()
    return 0


def _cmd_scan(args: argparse.Namespace) -> int:
    from .scan import scan

    _setup_logging("INFO")
    return scan(args.host, port=args.port, unit_id=args.unit_id,
                start=args.start, count=args.count)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="peugeot80", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="run the charge controller loop")
    p_run.add_argument("-c", "--config", default="config.yaml")
    p_run.set_defaults(func=_cmd_run)

    p_status = sub.add_parser("status", help="print current SoC and charger state once")
    p_status.add_argument("-c", "--config", default="config.yaml")
    p_status.set_defaults(func=_cmd_status)

    p_scan = sub.add_parser("scan", help="probe a Mennekes wallbox over Modbus TCP")
    p_scan.add_argument("host", help="wallbox IP address")
    p_scan.add_argument("--port", type=int, default=502)
    p_scan.add_argument("--unit-id", type=int, default=1)
    p_scan.add_argument("--start", type=int, default=0)
    p_scan.add_argument("--count", type=int, default=200)
    p_scan.set_defaults(func=_cmd_scan)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
