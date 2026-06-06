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
    from .notify import Notifier
    from .soc import build_soc_provider

    cfg = load_config(args.config)
    _setup_logging(cfg.get_path("logging.level", "INFO"))

    soc = build_soc_provider(cfg["soc"])
    charger = build_charger(cfg["charger"])
    notifier = Notifier(cfg.get("notify", {}))
    Controller(cfg, soc, charger, notifier=notifier).run()
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


def _cmd_simulate(args: argparse.Namespace) -> int:
    from .controller import Controller
    from .simulator import SimCharger, SimSoc, VirtualBattery

    _setup_logging("INFO")

    cfg = load_config(args.config) if args.config else {}
    limit = args.limit if args.limit is not None else float(cfg.get("charge_limit", 80))
    resume_below = float(cfg.get("resume_below", 0))
    hysteresis = float(cfg.get("hysteresis", 2))

    battery = VirtualBattery(percent=args.start, rate_pct_per_sec=args.rate)
    soc = SimSoc(battery)
    charger = SimCharger(battery)
    ctl = Controller(
        {
            "charge_limit": limit,
            "resume_below": resume_below,
            "hysteresis": hysteresis,
            "poll_interval": 1,
            "poll_interval_charging": 1,
        },
        soc,
        charger,
    )

    print(f"\n=== SIMULATIE: laden tot {limit:.0f}% (start {args.start:.0f}%, "
          f"{args.rate:.1f}%/s) ===\n")

    import time

    # Fase 1: laden tot de controller pauzeert bij de limiet.
    for _ in range(args.max_ticks):
        ctl.tick()
        if ctl._paused_at_limit:
            break
        time.sleep(args.tick_seconds)
    else:
        print("\nFOUT: limiet niet bereikt binnen max_ticks")
        return 1

    paused_at = battery.read_percent()
    print(f"\n--> Laden GEPAUZEERD bij {paused_at:.1f}%\n")

    # Fase 2: bevestig dat de SoC niet verder stijgt (paal staat echt uit).
    for _ in range(3):
        time.sleep(args.tick_seconds)
        ctl.tick()
    drift = battery.read_percent() - paused_at
    print(f"\n--> Na pauze stijgt SoC niet verder (drift {drift:+.2f}%)\n")

    # Fase 3: stekker eruit -> latch moet resetten voor de volgende sessie.
    print("--> Auto losgekoppeld (unplug)\n")
    battery.unplug()
    ctl.tick()

    ok = abs(drift) < 0.5 and not ctl._paused_at_limit
    print("=== RESULTAAT: " + ("GESLAAGD ✅" if ok else "MISLUKT ❌") + " ===\n")
    return 0 if ok else 1


class _Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


class _CapturingNotifier:
    def __init__(self) -> None:
        self.alerts: list[str] = []

    def alert(self, message: str) -> None:
        self.alerts.append(message)
        print(f"    🔔 ALARM: {message}")

    def info(self, message: str) -> None:
        pass


def _cmd_selftest(args: argparse.Namespace) -> int:
    """Drive the REAL controller through each failure case against the
    simulator (with injected faults) and report whether each watchdog fires."""
    import logging

    from .charger.base import ChargerState
    from .controller import Controller
    from .simulator import BrokenPauseCharger, SimCharger, SimSoc, VirtualBattery

    logging.basicConfig(level=logging.WARNING)
    results: list[tuple[str, bool]] = []

    def check(name: str, ok: bool) -> None:
        results.append((name, ok))
        print(f"  {'✅' if ok else '❌'} {name}\n")

    def controller(soc, charger, clock, notifier, **cfg):
        base = {"charge_limit": 80, "hysteresis": 2, "poll_interval": 1,
                "poll_interval_charging": 1}
        base.update(cfg)
        return Controller(base, soc, charger, notifier=notifier, clock=clock)

    print("\n=== SELFTEST: faalgevallen tegen de simulator ===\n")

    # 1. Stop-marge compenseert cloud-vertraging (stopt vroeg).
    print("[1] Stop-marge: limiet 80, marge 2 -> moet stoppen bij 78%")
    clk = _Clock()
    bat = VirtualBattery(percent=78.0, rate_pct_per_sec=0.0, clock=clk)
    ctl = controller(SimSoc(bat), SimCharger(bat), clk, _CapturingNotifier(), stop_margin=2)
    ctl.tick()
    check("stopt vroeg dankzij stop_margin", ctl._paused_at_limit)

    # 2. Pauze werkt niet (verkeerd register) -> SoC blijft stijgen -> alarm.
    print("[2] Kapotte pauze (verkeerd register): SoC blijft stijgen -> alarm")
    clk = _Clock()
    bat = VirtualBattery(percent=80.0, rate_pct_per_sec=1.0, clock=clk)
    notif = _CapturingNotifier()
    ctl = controller(SimSoc(bat), BrokenPauseCharger(bat), clk, notif, pause_tolerance=1.5)
    ctl.tick()                 # latch op 80 (pauze doet niets)
    clk.advance(3)             # 3s later -> ~83%
    ctl.tick()                 # detecteert dat SoC steeg -> alarm
    check("alarmeert 'pauze werkt niet'",
          any("pauze lijkt niet te werken" in a for a in notif.alerts))

    # 3. SoC-feed valt weg tijdens laden + on_soc_lost=pause -> preventief stop.
    print("[3] SoC-feed weg > soc_max_age, on_soc_lost=pause -> preventief stoppen")
    clk = _Clock()
    bat = VirtualBattery(percent=60.0, rate_pct_per_sec=0.0, clock=clk)
    charger = SimCharger(bat)
    notif = _CapturingNotifier()
    ctl = controller(SimSoc(bat, fail_after=1), charger, clk, notif,
                     soc_max_age=300, on_soc_lost="pause")
    ctl.tick()                 # ok lezing
    clk.advance(301)           # feed te oud
    ctl.tick()                 # stale -> alarm + preventieve pauze
    stale_alarm = any("niet leesbaar" in a for a in notif.alerts)
    check("alarmeert stale SoC en pauzeert preventief",
          stale_alarm and ctl._paused_at_limit)

    # 4. Korte hapering binnen soc_max_age -> GEEN vals alarm.
    print("[4] Korte SoC-hapering (< soc_max_age) -> geen vals alarm")
    clk = _Clock()
    bat = VirtualBattery(percent=60.0, rate_pct_per_sec=0.0, clock=clk)
    notif = _CapturingNotifier()
    ctl = controller(SimSoc(bat, fail_after=1), SimCharger(bat), clk, notif,
                     soc_max_age=300, on_soc_lost="hold")
    ctl.tick()
    clk.advance(60)            # korte hapering
    ctl.tick()
    check("geen vals alarm bij korte hapering", notif.alerts == [])

    # 5. Onzin-SoC (>100/NaN) wordt genegeerd, geen actie.
    print("[5] Onzin-SoC (150%) -> genegeerd, geen laadcommando")
    clk = _Clock()
    bat = VirtualBattery(percent=60.0, rate_pct_per_sec=0.0, clock=clk)
    charger = SimCharger(bat)

    class GarbageSoc(SimSoc):
        def read(self):
            from .soc.base import SocReading
            return SocReading(percent=150.0, charging=True)

    issued = {"resume": 0, "pause": 0}
    orig_pause, orig_resume = charger.pause, charger.resume
    charger.pause = lambda: (issued.__setitem__("pause", issued["pause"] + 1), orig_pause())[1]
    charger.resume = lambda: (issued.__setitem__("resume", issued["resume"] + 1), orig_resume())[1]
    ctl = controller(GarbageSoc(bat), charger, clk, _CapturingNotifier())
    ctl.tick()
    check("negeert onzin-SoC", issued == {"resume": 0, "pause": 0})

    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    print(f"=== SELFTEST RESULTAAT: {passed}/{total} "
          + ("GESLAAGD ✅" if passed == total else "MISLUKT ❌") + " ===\n")
    return 0 if passed == total else 1


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

    p_sim = sub.add_parser(
        "simulate",
        help="run the controller against a virtual battery+wallbox (no hardware)",
    )
    p_sim.add_argument("-c", "--config", default=None,
                       help="optional config.yaml to read charge_limit etc. from")
    p_sim.add_argument("--limit", type=float, default=None, help="override charge limit %%")
    p_sim.add_argument("--start", type=float, default=75.0, help="starting SoC %%")
    p_sim.add_argument("--rate", type=float, default=2.0, help="charge speed %%/sec")
    p_sim.add_argument("--tick-seconds", type=float, default=0.5,
                       help="real seconds between simulated ticks")
    p_sim.add_argument("--max-ticks", type=int, default=200)
    p_sim.set_defaults(func=_cmd_simulate)

    p_self = sub.add_parser(
        "selftest",
        help="drive the controller through each failure case (no hardware)",
    )
    p_self.set_defaults(func=_cmd_selftest)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
