from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time

from app.db.session import engine
from simulator.api import ChimeApi
from simulator.clickhouse import ClickHouseClient
from simulator.config import get_simulator_settings
from simulator.demo import LifecycleDemo
from simulator.seed import CatalogSeeder, SeedPlan
from simulator.traffic import run_traffic
from simulator.verify import verify


def _api() -> ChimeApi:
    settings = get_simulator_settings()
    return ChimeApi(settings.simulator_api_url, settings.simulator_http_timeout_seconds)


async def _seed(args: argparse.Namespace) -> int:
    plan = SeedPlan(
        users=args.users,
        artists=args.artists,
        albums=args.albums,
        min_track_seconds=args.min_track_seconds,
        max_track_seconds=args.max_track_seconds,
        concurrency=args.concurrency,
        seed=args.seed,
    )
    started = time.perf_counter()
    async with _api() as api:
        await api.health()
        report = await CatalogSeeder(api, plan).run()
    print(
        f"seeded in {time.perf_counter() - started:.1f}s: users={report.users} artists={report.artists} "
        f"albums={report.albums} tracks={report.tracks} track_artists={report.track_artists} "
        f"media_assets={report.media_assets} uploaded={report.uploaded_bytes / 1_048_576:.1f} MiB"
    )
    for failure in report.failures:
        print(f"  failed upload: {failure}")
    return 0 if not report.failures else 1


async def _run(args: argparse.Namespace) -> int:
    async with _api() as api:
        stats = await run_traffic(
            api,
            duration_seconds=args.duration,
            listeners=args.listeners,
            studio_interval=args.studio_interval,
            speed=args.speed,
            seed=args.seed,
        )
    print(
        f"sessions={stats.sessions} plays={stats.plays} completed={stats.completed} seeks={stats.seeks} "
        f"range_requests={stats.range_requests} streamed={stats.streamed_bytes / 1_048_576:.1f} MiB "
        f"listened={stats.listened_ms / 1000:.0f}s events={stats.events} errors={dict(stats.errors)}"
    )
    for operation, count in sorted(stats.studio_ops.items()):
        print(f"  studio {operation}: {count}")
    return 0


async def _verify(args: argparse.Namespace) -> int:
    async with ClickHouseClient(get_simulator_settings()) as ch:
        checks, pipeline = await verify(ch, wait_seconds=args.wait)
    print(f"{'check':<24}{'postgres':>12}{'clickhouse':>12}{'rustfs':>12}  status")
    for check in checks:
        print(
            f"{check.name:<24}{check.postgres:>12}{'' if check.clickhouse is None else check.clickhouse:>12}"
            f"{'' if check.storage is None else check.storage:>12}  {'OK' if check.ok else 'MISMATCH'}"
        )
    print()
    for name, value in pipeline.items():
        print(f"{name:<24}{value}")
    return 0 if all(c.ok for c in checks) else 1


async def _demo(args: argparse.Namespace) -> int:
    async with _api() as api, ClickHouseClient(get_simulator_settings()) as ch:
        await LifecycleDemo(api, ch, timeout=args.timeout).run()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m simulator",
        description="Chime data generator and load simulator",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    seed = commands.add_parser(
        "seed", help="fill PostgreSQL and RustFS with a synthetic catalog via the API"
    )
    seed.add_argument("--users", type=int, default=60)
    seed.add_argument("--artists", type=int, default=16)
    seed.add_argument("--albums", type=int, default=20)
    seed.add_argument("--min-track-seconds", type=int, default=15)
    seed.add_argument("--max-track-seconds", type=int, default=45)
    seed.add_argument("--concurrency", type=int, default=6)
    seed.add_argument("--seed", type=int, default=42)
    seed.set_defaults(handler=_seed)

    run = commands.add_parser(
        "run", help="simulate production traffic of app clients and the studio"
    )
    run.add_argument("--duration", type=float, default=300.0, help="seconds")
    run.add_argument(
        "--listeners", type=int, default=12, help="concurrent client sessions"
    )
    run.add_argument(
        "--studio-interval",
        type=float,
        default=4.0,
        help="seconds between catalog changes, 0 disables",
    )
    run.add_argument(
        "--speed",
        type=float,
        default=1.0,
        help="playback and think-time acceleration, 1 = real time",
    )
    run.add_argument("--seed", type=int, default=7)
    run.set_defaults(handler=_run)

    check = commands.add_parser(
        "verify", help="reconcile PostgreSQL, ClickHouse and RustFS"
    )
    check.add_argument(
        "--wait", type=float, default=20.0, help="seconds to wait for CDC convergence"
    )
    check.set_defaults(handler=_verify)

    demo = commands.add_parser(
        "demo", help="walk through insert/update/move/delete and show DWH propagation"
    )
    demo.add_argument("--timeout", type=float, default=30.0)
    demo.set_defaults(handler=_demo)
    console = commands.add_parser("tui", help="interactive console (temporary tooling)")
    console.set_defaults(handler=None)
    return parser


async def _main(args: argparse.Namespace) -> int:
    try:
        return await args.handler(args)
    finally:
        await engine.dispose()


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    args = build_parser().parse_args()
    if args.command == "tui":
        from simulator.tui import run_console

        run_console()
        return
    sys.exit(asyncio.run(_main(args)))


if __name__ == "__main__":
    main()
