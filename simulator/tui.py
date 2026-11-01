from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
import questionary
from questionary import Choice, Separator
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

from app.db.session import engine
from simulator.api import STUDIO_DEVICE, ApiError, ChimeApi
from simulator.clickhouse import ClickHouseClient
from simulator.config import get_simulator_settings
from simulator.maintenance import wipe_all
from simulator.operations import SongDraft, create_song
from simulator.seed import CatalogSeeder, SeedPlan
from simulator.traffic import TrafficStats, _fetch_all, run_traffic
from simulator.verify import verify

LOAD_LISTENERS = 15
LOAD_SPEED = 1.0
LOAD_STUDIO_INTERVAL = 3.0
NEW = "__new__"
BACK = "__back__"

console = Console()


def _human_bytes(size: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024:
            return f"{size:.0f} {unit}"
        size /= 1024
    return f"{size:.1f} TiB"


async def select(message: str, choices: list[Any], search: bool = False) -> Any:
    return await questionary.select(
        message,
        choices=choices,
        use_search_filter=search,
        use_jk_keys=not search,
        instruction="(↑↓, Enter" + (", печатайте для поиска)" if search else ")"),
    ).ask_async()


async def ask(message: str, default: str = "") -> str | None:
    answer = await questionary.text(message, default=default).ask_async()
    return answer.strip() if answer is not None else None


async def confirm(message: str) -> bool:
    return bool(await questionary.confirm(message, default=False).ask_async())


class DataConsole:
    def __init__(self) -> None:
        settings = get_simulator_settings()
        self.api = ChimeApi(
            settings.simulator_api_url, settings.simulator_http_timeout_seconds
        )
        self.ch = ClickHouseClient(settings)

    async def run(self) -> None:
        console.print("[bold]chime[/] · консоль данных  [dim](Ctrl+C — выход)[/]\n")
        menu = {
            "Треки": self.tracks,
            "Добавить трек": self.add_song,
            "Добавить пользователя": self.add_user,
            "Нагрузка (симуляция пользователей)": self.load,
            "Сгенерировать каталог": self.seed,
            "Сверка PostgreSQL ↔ ClickHouse ↔ RustFS": self.verify,
            "Очистить все базы": self.wipe,
            "Очистить и сгенерировать заново": self.reseed,
        }
        while True:
            choice = await select("Что сделать?", [*menu, Separator(), "Выход"])
            if choice in (None, "Выход"):
                return
            try:
                await menu[choice]()
            except (ApiError, ValueError, httpx.HTTPError) as exc:
                console.print(f"[red]ошибка:[/] {exc}")
            console.print()

    async def tracks(self) -> None:
        tracks = await _fetch_all(self.api.list_tracks, STUDIO_DEVICE)
        if not tracks:
            console.print("[dim]треков нет[/]")
            return
        tracks.sort(key=lambda t: t["updated_at"], reverse=True)
        choices = [
            Choice(
                f"{t['title'][:50]:<52}{t['status']:<11}{t['duration_seconds']:>4} с",
                t["track_id"],
            )
            for t in tracks
        ]
        track_id = await select(
            f"Трек ({len(tracks)})", [Choice("← назад", BACK), *choices], search=True
        )
        if track_id in (None, BACK):
            return
        await self.track(track_id)

    async def track(self, track_id: str) -> None:
        detail, media = await asyncio.gather(
            self.api.get_track(track_id, STUDIO_DEVICE),
            self.api.list_track_media(track_id, STUDIO_DEVICE),
        )
        album = detail["album"]
        card = Table.grid(padding=(0, 2))
        card.add_column(style="dim")
        card.add_column()
        card.add_row("трек", f"[bold]{detail['title']}[/]")
        card.add_row(
            "исполнители",
            ", ".join(f"{a['name']} ({a['role']})" for a in detail["artists"]) or "—",
        )
        card.add_row(
            "релиз",
            f"{album['title']} · {album['album_type']} · {album['status']}"
            if album
            else "—",
        )
        card.add_row("статус", detail["status"])
        card.add_row("длительность", f"{detail['duration_seconds']} с")
        card.add_row("explicit", "да" if detail["explicit_content"] else "нет")
        card.add_row("изменён", detail["updated_at"][:19].replace("T", " "))
        card.add_row("id", detail["track_id"])
        console.print(card)
        files = Table(
            "тип",
            "формат",
            "размер",
            "статус",
            box=None,
            header_style="dim",
            padding=(0, 2),
        )
        for asset in media:
            fmt = (
                f"{asset['audio_format']} {asset['audio_bitrate']} kbps"
                if asset["audio_bitrate"]
                else asset["mime_type"]
                + (f" · {asset['language']}" if asset["language"] else "")
            )
            files.add_row(
                asset["asset_type"],
                fmt,
                _human_bytes(asset["size_bytes"]),
                asset["status"],
            )
        console.print(files)
        lyrics = next(
            (a for a in media if a["asset_type"] == "lyrics" and a["download_url"]),
            None,
        )
        if lyrics and await confirm("Показать текст песни?"):
            text = await self.api.fetch_text(lyrics["download_url"])
            console.print(f"\n[dim]язык: {lyrics['language'] or '—'}[/]\n{text}")

    async def add_song(self) -> None:
        title = await ask("Название трека:")
        if not title:
            return
        artists = await _fetch_all(self.api.list_artists, STUDIO_DEVICE)
        artist = await select(
            "Исполнитель",
            [
                Choice("+ новый исполнитель", NEW),
                *(
                    Choice(a["name"], a["artist_id"])
                    for a in sorted(artists, key=lambda a: a["name"])
                ),
            ],
            search=True,
        )
        if artist is None:
            return
        new_artist = await ask("Имя нового исполнителя:") if artist == NEW else None
        if artist == NEW and not new_artist:
            return
        albums = await _fetch_all(self.api.list_albums, STUDIO_DEVICE)
        album = await select(
            "Релиз",
            [
                Choice("+ новый сингл", NEW),
                *(
                    Choice(f"{a['title']} · {a['album_type']}", a["album_id"])
                    for a in sorted(albums, key=lambda a: a["title"])
                ),
            ],
            search=True,
        )
        if album is None:
            return
        seconds = await ask("Длительность, секунд:", "30")
        if not seconds or not seconds.isdigit():
            return
        lyrics = await ask("Текст песни (строки через « / », пусто — без текста):")
        publish = await confirm("Опубликовать сразу?")
        with console.status("синтез аудио и загрузка через API…"):
            track = await create_song(
                self.api,
                SongDraft(
                    title=title,
                    seconds=max(1, min(int(seconds), 600)),
                    artist_id=None if artist == NEW else artist,
                    new_artist_name=new_artist,
                    album_id=None if album == NEW else album,
                    publish=publish,
                    lyrics="\n".join(line.strip() for line in lyrics.split("/"))
                    if lyrics
                    else None,
                ),
            )
        console.print(
            f"[green]создан трек[/] «{track['title']}» · {track['duration_seconds']} с · {track['status']}"
        )
        await self.track(track["track_id"])

    async def add_user(self) -> None:
        email = await ask("Email:")
        if not email:
            return
        user = await self.api.create_user(email)
        console.print(f"[green]создан пользователь[/] {user['email']} · {user['id']}")

    async def load(self) -> None:
        console.print(
            f"[green]нагрузка запущена[/]: {LOAD_LISTENERS} слушателей + студия, реальное время. "
            "[bold]Enter — остановить[/]\n"
        )
        handler = RichHandler(
            console=console,
            show_path=False,
            show_level=False,
            markup=False,
            log_time_format="%H:%M:%S",
        )
        sim_logger = logging.getLogger("simulator")
        sim_logger.addHandler(handler)
        sim_logger.setLevel(logging.DEBUG)
        stop = asyncio.Event()
        stats = TrafficStats()
        traffic = asyncio.create_task(
            run_traffic(
                self.api,
                duration_seconds=None,
                listeners=LOAD_LISTENERS,
                studio_interval=LOAD_STUDIO_INTERVAL,
                speed=LOAD_SPEED,
                seed=0,
                progress_every=60.0,
                stop=stop,
                stats=stats,
            )
        )
        waiter = asyncio.create_task(asyncio.to_thread(input))
        try:
            await asyncio.wait({traffic, waiter}, return_when=asyncio.FIRST_COMPLETED)
            stop.set()
            console.print("[yellow]остановка…[/]")
            await traffic
        finally:
            sim_logger.removeHandler(handler)
            sim_logger.setLevel(logging.INFO)
        console.print(
            f"\n[bold]итого[/]: сессий {stats.sessions} · плеев {stats.plays} ({stats.completed} до конца) · "
            f"перемоток {stats.seeks} · стрим {_human_bytes(stats.streamed_bytes)} в {stats.range_requests} Range-запросах · "
            f"прослушано {stats.listened_ms // 1000} с · событий {stats.events} · "
            f"операций студии {sum(stats.studio_ops.values())} · ошибки {dict(stats.errors) or 0}"
        )
        if not waiter.done():
            console.print("[dim]нажмите Enter[/]")
            await waiter

    async def seed(self) -> None:
        values = []
        for question, default in (
            ("Пользователей:", "60"),
            ("Исполнителей:", "16"),
            ("Релизов:", "20"),
        ):
            answer = await ask(question, default)
            if not answer or not answer.isdigit():
                return
            values.append(int(answer))
        await self._seed(
            SeedPlan(
                users=values[0], artists=max(1, values[1]), albums=max(1, values[2])
            )
        )

    async def _seed(self, plan: SeedPlan) -> None:
        with console.status(
            f"генерация: {plan.users} пользователей, {plan.artists} исполнителей, {plan.albums} релизов…"
        ):
            report = await CatalogSeeder(self.api, plan).run()
        console.print(
            f"[green]каталог готов[/]: пользователей {report.users}, треков {report.tracks}, связей {report.track_artists}, "
            f"медиа {report.media_assets} ({_human_bytes(report.uploaded_bytes)})"
            + (f", [red]ошибок {len(report.failures)}[/]" if report.failures else "")
        )

    async def verify(self) -> None:
        with console.status("сверка…"):
            checks, pipeline = await verify(self.ch, wait_seconds=10.0)
        table = Table(
            "проверка",
            "PostgreSQL",
            "ClickHouse / RustFS",
            "",
            box=None,
            header_style="dim",
            padding=(0, 2),
        )
        for check in checks:
            other = check.clickhouse if check.clickhouse is not None else check.storage
            table.add_row(
                check.name,
                str(check.postgres),
                str(other),
                "[green]OK[/]" if check.ok else "[red]MISMATCH[/]",
            )
        console.print(table)
        console.print(
            "[dim]" + "  ".join(f"{k}={v}" for k, v in pipeline.items()) + "[/]"
        )

    async def wipe(self) -> bool:
        if not await confirm("Удалить ВСЕ данные PostgreSQL, RustFS и ClickHouse?"):
            return False
        with console.status(
            "очистка: RustFS → PostgreSQL → ожидание CDC → ClickHouse…"
        ):
            report = await wipe_all(self.ch)
        console.print(
            f"[green]очищено[/]: объектов {report.storage_objects}, строк PG {report.postgres_rows}, "
            f"таблиц CH {report.clickhouse_tables}"
            + ("" if report.cdc_drained else " [yellow](CDC не успел догнать)[/]")
        )
        return True

    async def reseed(self) -> None:
        if await self.wipe():
            await self._seed(SeedPlan())
            await self.verify()

    async def close(self) -> None:
        await self.api.__aexit__()
        await self.ch.__aexit__()
        await engine.dispose()


async def _main() -> None:
    app = DataConsole()
    try:
        await app.run()
    finally:
        await app.close()


def run_console() -> None:
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        pass
