from __future__ import annotations

import json
import os
import shutil
import sys
import webbrowser
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import yt_dlp
from yt_dlp.utils import DownloadError
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    Progress,
    BarColumn,
    TextColumn,
    DownloadColumn,
    TransferSpeedColumn,
    TimeRemainingColumn,
)
from rich.prompt import Prompt, Confirm
from rich.table import Table
from rich.text import Text


# ============================================================
# CONFIGURATION
# ============================================================

APP_NAME = "YT TERMINAL"
APP_VERSION = "0.1.0"

APP_DIR = Path.home() / ".yt_terminal_downloader"
CONFIG_FILE = APP_DIR / "config.json"
HISTORY_FILE = APP_DIR / "history.json"

DEFAULT_DOWNLOAD_DIR = Path.home() / "Downloads" / "YouTube"

console = Console()


# ============================================================
# CONFIGURATION AND HISTORY
# ============================================================

def ensure_app_dir() -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)


def load_json(path: Path, default):
    try:
        with path.open("r", encoding="utf-8") as file:
            return json.load(file)
    except (OSError, json.JSONDecodeError):
        return default


def save_json(path: Path, data) -> None:
    ensure_app_dir()

    temporary_file = path.with_suffix(path.suffix + ".tmp")

    with temporary_file.open("w", encoding="utf-8") as file:
        json.dump(data, file, indent=2, ensure_ascii=False)

    temporary_file.replace(path)


def get_download_dir() -> Path:
    config = load_json(CONFIG_FILE, {})

    saved_path = config.get("download_dir")

    if saved_path:
        folder = Path(saved_path).expanduser()
    else:
        folder = DEFAULT_DOWNLOAD_DIR

    try:
        folder.mkdir(parents=True, exist_ok=True)
        return folder
    except OSError:
        console.print(
            "[yellow]Could not access the saved folder. "
            "Using the default download directory.[/yellow]"
        )

        DEFAULT_DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
        return DEFAULT_DOWNLOAD_DIR


def set_download_dir(folder: Path) -> None:
    folder = folder.expanduser().resolve()
    folder.mkdir(parents=True, exist_ok=True)

    config = load_json(CONFIG_FILE, {})
    config["download_dir"] = str(folder)

    save_json(CONFIG_FILE, config)


def get_history() -> list:
    history = load_json(HISTORY_FILE, [])
    return history if isinstance(history, list) else []


def add_history(
    title: str,
    url: str,
    mode: str,
    quality: str,
    status: str,
    file_path: str = "",
) -> None:
    history = get_history()

    history.insert(
        0,
        {
            "date": datetime.now().astimezone().isoformat(
                timespec="seconds"
            ),
            "title": title,
            "url": url,
            "mode": mode,
            "quality": quality,
            "status": status,
            "file": file_path,
        },
    )

    # Keep the latest 200 entries.
    save_json(HISTORY_FILE, history[:200])


# ============================================================
# TERMINAL UI
# ============================================================

def clear_screen() -> None:
    os.system("cls" if os.name == "nt" else "clear")



def show_header() -> None:
    title = Text(APP_NAME, style="bold cyan")
    title.append(f"  v{APP_VERSION}", style="dim")

    console.print(
        Panel(
            title,
            subtitle="YouTube downloader for Windows CMD",
            border_style="cyan",
            padding=(1, 3),
        )
    )


def show_menu() -> None:
    download_dir = get_download_dir()

    table = Table(
        title="MAIN MENU",
        border_style="bright_blue",
        header_style="bold cyan",
        expand=True,
    )

    table.add_column("Option", style="bold", width=10)
    table.add_column("Action")
    table.add_column("Description")

    table.add_row("1", "Download video", "Choose a maximum resolution")
    table.add_row("2", "Download audio", "Extract audio as MP3")
    table.add_row("3", "Preview video", "Inspect title, duration and quality")
    table.add_row("4", "Change folder", "Choose where downloads are saved")
    table.add_row("5", "Open folder", "Open the download directory")
    table.add_row("6", "History", "View recent download attempts")
    table.add_row("7", "Exit", "Close the application")

    console.print(table)
    console.print(f"\n[dim]Download folder: {download_dir}[/dim]\n")


def pause() -> None:
    console.input("\n[dim]Press Enter to continue...[/dim]")


def is_ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def validate_youtube_url(url: str) -> bool:
    url = url.strip().strip("\"'")

    try:
        parsed = urlparse(url)
    except ValueError:
        return False

    if parsed.scheme not in ("http", "https"):
        return False

    host = (parsed.hostname or "").lower()

    return (
        host == "youtu.be"
        or host.endswith(".youtu.be")
        or host == "youtube.com"
        or host.endswith(".youtube.com")
        or host == "youtube-nocookie.com"
        or host.endswith(".youtube-nocookie.com")
    )


def ask_for_url() -> str | None:
    url = Prompt.ask("[bold]Paste the YouTube URL[/bold]").strip()
    url = url.strip("\"'")

    if not url:
        console.print("[yellow]No URL entered.[/yellow]")
        return None

    if not validate_youtube_url(url):
        console.print(
            "[red]Invalid URL.[/red] Enter a complete YouTube URL, "
            "such as https://www.youtube.com/watch?v=..."
        )
        return None

    return url


# ============================================================
# VIDEO INFORMATION
# ============================================================

def format_duration(seconds) -> str:
    if not isinstance(seconds, (int, float)):
        return "Unknown"

    seconds = int(seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)

    if hours:
        return f"{hours}:{minutes:02}:{seconds:02}"

    return f"{minutes}:{seconds:02}"


def get_video_info(url: str) -> dict | None:
    options = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "skip_download": True,
        "socket_timeout": 20,
        "retries": 2,
    }

    try:
        with console.status(
            "[cyan]Fetching video information...[/cyan]",
            spinner="dots",
        ):
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=False)

        if not info:
            console.print("[red]No video information was returned.[/red]")
            return None

        return info

    except Exception as error:
        show_error(error)
        return None


def available_resolutions(info: dict) -> list[int]:
    heights = set()

    for fmt in info.get("formats", []):
        if fmt.get("vcodec") == "none":
            continue

        height = fmt.get("height")

        if isinstance(height, int):
            heights.add(height)

    return sorted(heights, reverse=True)


def show_video_info(info: dict) -> None:
    table = Table(
        title="VIDEO INFORMATION",
        border_style="cyan",
        show_header=False,
        expand=True,
    )

    table.add_column("Field", style="bold cyan", width=18)
    table.add_column("Value", overflow="fold")

    table.add_row("Title", str(info.get("title") or "Unknown"))
    table.add_row("Channel", str(info.get("uploader") or "Unknown"))
    table.add_row(
        "Duration",
        format_duration(info.get("duration")),
    )
    table.add_row(
        "Views",
        f"{info['view_count']:,}"
        if isinstance(info.get("view_count"), int)
        else "Unknown",
    )

    resolutions = available_resolutions(info)

    table.add_row(
        "Video resolutions",
        ", ".join(f"{height}p" for height in resolutions)
        if resolutions
        else "Not reported",
    )

    console.print(table)


def preview_video() -> None:
    url = ask_for_url()

    if not url:
        return

    info = get_video_info(url)

    if info:
        show_video_info(info)

    pause()


# ============================================================
# QUALITY SELECTION
# ============================================================

def choose_quality() -> int | None:
    table = Table(
        title="SELECT MAXIMUM RESOLUTION",
        border_style="cyan",
        show_header=True,
    )

    table.add_column("Option", style="bold")
    table.add_column("Resolution")
    table.add_column("Meaning")

    table.add_row("1", "Best available", "No resolution limit")
    table.add_row("2", "1080p", "Full HD maximum")
    table.add_row("3", "720p", "HD maximum")
    table.add_row("4", "480p", "Standard definition maximum")
    table.add_row("5", "360p", "Lower bandwidth maximum")

    console.print(table)

    choice = Prompt.ask(
        "Choose quality",
        choices=["1", "2", "3", "4", "5"],
        default="1",
    )

    return {
        "1": None,
        "2": 1080,
        "3": 720,
        "4": 480,
        "5": 360,
    }[choice]


def build_format(quality: int | None, ffmpeg: bool) -> str:
    if quality is None:
        if ffmpeg:
            return "bv*+ba/b"

        # Without FFmpeg, prefer a single-file format that
        # does not need separate video/audio streams merged.
        return "best[ext=mp4]/best"

    if ffmpeg:
        return (
            f"bestvideo[height<={quality}]+bestaudio/"
            f"best[height<={quality}]"
        )

    # Keep the selected resolution limit without requiring
    # FFmpeg to merge separate streams.
    return (
        f"best[height<={quality}][ext=mp4]/"
        f"best[height<={quality}]"
    )


# ============================================================
# DOWNLOAD PROGRESS
# ============================================================

def download_video(
    url: str,
    audio: bool = False,
    quality: int | None = None,
) -> None:
    download_dir = get_download_dir()
    ffmpeg = is_ffmpeg_available()

    if audio and not ffmpeg:
        console.print(
            Panel(
                "MP3 conversion requires FFmpeg, but FFmpeg was not "
                "found on PATH.\n\n"
                "Install FFmpeg, reopen CMD, and run this application again.",
                title="FFmpeg required",
                border_style="red",
            )
        )
        pause()
        return

    if not audio and not ffmpeg:
        console.print(
            "[yellow]FFmpeg was not detected. "
            "The downloader will prefer single-file video formats. "
            "Some high-resolution downloads may be unavailable.[/yellow]\n"
        )

    info = get_video_info(url)

    if not info:
        pause()
        return

    show_video_info(info)

    if audio:
        mode = "MP3"
        quality_label = "Best audio available"
    else:
        mode = "Video"
        quality_label = (
            "Best available"
            if quality is None
            else f"Up to {quality}p"
        )

    console.print(f"\n[cyan]Mode:[/cyan] {mode}")
    console.print(f"[cyan]Quality:[/cyan] {quality_label}")
    console.print(f"[cyan]Save to:[/cyan] {download_dir}")

    if not Confirm.ask("\nStart download?", default=True):
        console.print("[yellow]Download cancelled.[/yellow]")
        pause()
        return

    output_template = str(
        download_dir / "%(title).180B [%(id)s].%(ext)s"
    )

    progress = Progress(
        TextColumn("[bold cyan]{task.description}"),
        BarColumn(bar_width=30),
        TextColumn("{task.percentage:>5.1f}%"),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
        console=console,
        transient=False,
    )

    task_id = progress.add_task(
        "Preparing",
        total=None,
        status="Starting",
    )

    downloaded_file = ""

    def progress_hook(data: dict) -> None:
        nonlocal downloaded_file

        status = data.get("status", "")

        if status == "downloading":
            downloaded = data.get("downloaded_bytes", 0)
            total = (
                data.get("total_bytes")
                or data.get("total_bytes_estimate")
            )
            speed = data.get("speed")

            progress.update(
                task_id,
                total=total,
                completed=downloaded,
                speed=speed,
                status="Downloading",
                description="Downloading",
            )

        elif status == "finished":
            downloaded_file = data.get("filename", "")

            progress.update(
                task_id,
                completed=progress.tasks[0].completed,
                status="Processing",
                description="Processing",
            )

        elif status == "error":
            progress.update(
                task_id,
                status="Error",
                description="Download error",
            )

    options = {
        "format": (
            "bestaudio/best"
            if audio
            else build_format(quality, ffmpeg)
        ),
        "outtmpl": output_template,
        "windowsfilenames": True,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "retries": 3,
        "fragment_retries": 3,
        "socket_timeout": 30,
        "progress_hooks": [progress_hook],
    }

    if audio:
        options["postprocessors"] = [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ]
    elif ffmpeg:
        options["merge_output_format"] = "mp4"

    success = False

    try:
        with progress:
            with yt_dlp.YoutubeDL(options) as ydl:
                result = ydl.download([url])

        if result == 0:
            success = True
            progress.update(
                task_id,
                status="Complete",
                description="Complete",
            )
        else:
            raise DownloadError(
                f"yt-dlp returned exit code {result}."
            )

    except Exception as error:
        console.print()
        show_error(error)

    if success:
        console.print(
            Panel(
                "Download completed successfully.",
                title="Success",
                border_style="green",
            )
        )

        add_history(
            title=str(info.get("title") or "Unknown"),
            url=url,
            mode=mode,
            quality=quality_label,
            status="Success",
            file_path=downloaded_file,
        )
    else:
        add_history(
            title=str(info.get("title") or "Unknown"),
            url=url,
            mode=mode,
            quality=quality_label,
            status="Failed",
            file_path=downloaded_file,
        )

    pause()


# ============================================================
# ERROR DISPLAY
# ============================================================

def show_error(error: Exception) -> None:
    message = str(error).strip()

    if not message:
        message = type(error).__name__

    console.print(
        Panel(
            message[:2000],
            title="Something went wrong",
            border_style="red",
        )
    )

    console.print(
        "[dim]Check the URL and internet connection. "
        "Some videos may be private, restricted, removed, "
        "or unavailable in your region.[/dim]"
    )


# ============================================================
# DOWNLOAD FOLDER
# ============================================================

def change_download_folder() -> None:
    current = get_download_dir()

    console.print(f"Current folder: [cyan]{current}[/cyan]")
    entered = Prompt.ask(
        "Enter a new folder path",
        default=str(current),
    ).strip().strip("\"'")

    if not entered:
        console.print("[yellow]Folder was not changed.[/yellow]")
        return

    try:
        folder = Path(entered).expanduser()
        set_download_dir(folder)

        console.print(
            f"[green]Download folder saved:[/green] "
            f"{folder.resolve()}"
        )
    except OSError as error:
        show_error(error)

    pause()


def open_download_folder() -> None:
    folder = get_download_dir()

    try:
        if sys.platform == "win32":
            os.startfile(str(folder))
        else:
            console.print(
                "[yellow]Automatic folder opening is designed "
                "for Windows.[/yellow]"
            )
    except OSError as error:
        show_error(error)

    pause()


# ============================================================
# HISTORY
# ============================================================

def show_history() -> None:
    history = get_history()

    if not history:
        console.print("[yellow]No download history yet.[/yellow]")
        pause()
        return

    table = Table(
        title="RECENT DOWNLOADS",
        border_style="cyan",
        expand=True,
    )

    table.add_column("Date", width=20)
    table.add_column("Title", ratio=3, overflow="fold")
    table.add_column("Mode", width=8)
    table.add_column("Quality", width=20)
    table.add_column("Status", width=10)

    for item in history[:20]:
        date = str(item.get("date", "Unknown"))
        title = str(item.get("title", "Unknown"))
        mode = str(item.get("mode", "Unknown"))
        quality = str(item.get("quality", "Unknown"))
        status = str(item.get("status", "Unknown"))

        if status == "Success":
            status_display = f"[green]{status}[/green]"
        else:
            status_display = f"[red]{status}[/red]"

        table.add_row(
            date[:19].replace("T", " "),
            title,
            mode,
            quality,
            status_display,
        )

    console.print(table)
    pause()

# ============================================================
# MAIN APPLICATION
# ============================================================

def main() -> None:
    try:
        ensure_app_dir()
        get_download_dir()

        while True:
            clear_screen()
            show_header()
            show_menu()

            choice = Prompt.ask(
                "Select an option",
                choices=["1", "2", "3", "4", "5", "6", "7"],
                default="1",
            )

            if choice == "1":
                url = ask_for_url()

                if url:
                    quality = choose_quality()
                    download_video(
                        url,
                        audio=False,
                        quality=quality,
                    )
                else:
                    pause()

            elif choice == "2":
                url = ask_for_url()

                if url:
                    download_video(url, audio=True)
                else:
                    pause()

            elif choice == "3":
                preview_video()

            elif choice == "4":
                change_download_folder()

            elif choice == "5":
                open_download_folder()

            elif choice == "6":
                show_history()

            elif choice == "7":
                console.print("[cyan]Goodbye.[/cyan]")
                break

    except KeyboardInterrupt:
        console.print("\n[yellow]Application interrupted.[/yellow]")

    except Exception as error:
        show_error(error)
        sys.exit(1)


if __name__ == "__main__":
    main()