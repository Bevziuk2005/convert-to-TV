#!/usr/bin/env python3
"""
Конвертація «сирих» фільмів (часто MPEG-TS під розширенням .mp4) у справжній MP4
для телевізора. Якщо відео вже H.264, а звук AAC — лише перемультиплексування
без перекодування (без втрати якості). Інакше — перекодування з високою якістю.

Потрібен ffmpeg: brew install ffmpeg
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# --- Налаштування за замовчуванням ---
DEFAULT_INPUT = Path.home() / "Downloads" / "сирі фільми"
DEFAULT_OUTPUT = Path.home() / "Downloads" / "Спільні"

VIDEO_EXTENSIONS = {".mp4", ".ts", ".mkv", ".avi", ".mov", ".m4v", ".mpeg", ".mpg"}


@dataclass
class MediaInfo:
    path: Path
    format_name: str
    video_codec: str | None
    audio_codec: str | None
    width: int | None
    height: int | None


def find_ffmpeg() -> Path:
    exe = shutil.which("ffmpeg")
    if not exe:
        print(
            "Помилка: ffmpeg не знайдено. Встановіть: brew install ffmpeg",
            file=sys.stderr,
        )
        sys.exit(1)
    return Path(exe)


def find_ffprobe() -> Path:
    exe = shutil.which("ffprobe")
    if not exe:
        print(
            "Помилка: ffprobe не знайдено (йде разом із ffmpeg).",
            file=sys.stderr,
        )
        sys.exit(1)
    return Path(exe)


def probe(ffprobe: Path, src: Path) -> MediaInfo:
    cmd = [
        str(ffprobe),
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(src),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    data = json.loads(result.stdout)

    fmt = data.get("format", {}).get("format_name", "unknown")
    video_codec = audio_codec = None
    width = height = None

    for stream in data.get("streams", []):
        if stream.get("codec_type") == "video" and video_codec is None:
            video_codec = stream.get("codec_name")
            width = stream.get("width")
            height = stream.get("height")
        elif stream.get("codec_type") == "audio" and audio_codec is None:
            audio_codec = stream.get("codec_name")

    return MediaInfo(
        path=src,
        format_name=fmt,
        video_codec=video_codec,
        audio_codec=audio_codec,
        width=width,
        height=height,
    )


def can_remux_lossless(info: MediaInfo) -> bool:
    """H.264 + AAC у контейнері MP4 — стандарт для більшості телевізорів."""
    if info.video_codec != "h264":
        return False
    if info.audio_codec not in ("aac", "mp4a"):
        return False
    return True


def build_ffmpeg_cmd(
    ffmpeg: Path,
    src: Path,
    dst: Path,
    remux_only: bool,
) -> list[str]:
    cmd = [str(ffmpeg), "-hide_banner", "-y", "-i", str(src)]

    if remux_only:
        cmd += [
            "-map",
            "0:v:0?",
            "-map",
            "0:a:0?",
            "-c",
            "copy",
            "-movflags",
            "+faststart",
        ]
    else:
        # Висока якість, якщо довелося перекодувати
        cmd += [
            "-map",
            "0:v:0?",
            "-map",
            "0:a:0?",
            "-c:v",
            "libx264",
            "-preset",
            "slow",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-profile:v",
            "high",
            "-level",
            "4.1",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
        ]

    cmd.append(str(dst))
    return cmd


def convert_one(
    ffmpeg: Path,
    ffprobe: Path,
    src: Path,
    out_dir: Path,
    *,
    skip_existing: bool = True,
) -> bool:
    dst = out_dir / f"{src.stem}.mp4"
    if skip_existing and dst.exists() and dst.stat().st_mtime >= src.stat().st_mtime:
        print(f"⏭  Пропуск (вже є): {dst.name}")
        return True

    try:
        info = probe(ffprobe, src)
    except subprocess.CalledProcessError as e:
        print(f"✗  Не вдалося прочитати: {src.name}\n   {e.stderr}", file=sys.stderr)
        return False

    remux = can_remux_lossless(info)
    mode = "копія без перекодування" if remux else "перекодування (CRF 18)"
    res = f"{info.width}x{info.height}" if info.width else "?"
    print(f"▶  {src.name}")
    print(f"   формат: {info.format_name} | v: {info.video_codec} | a: {info.audio_codec} | {res}")
    print(f"   режим: {mode} → {dst.name}")

    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = build_ffmpeg_cmd(ffmpeg, src, dst, remux_only=remux)

    proc = subprocess.run(cmd)
    if proc.returncode != 0:
        print(f"✗  Помилка ffmpeg для: {src.name}", file=sys.stderr)
        if dst.exists():
            dst.unlink()
        return False

    print(f"✓  Готово: {dst}")
    return True


def collect_sources(input_dir: Path) -> list[Path]:
    if not input_dir.is_dir():
        return []
    files = [
        p
        for p in sorted(input_dir.iterdir())
        if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS and not p.name.startswith(".")
    ]
    return files


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Перетворення сирих фільмів у MP4 для телевізора (H.264/AAC, faststart)."
    )
    parser.add_argument(
        "-i",
        "--input",
        type=Path,
        default=DEFAULT_INPUT,
        help=f"Папка з вихідними файлами (за замовч.: {DEFAULT_INPUT})",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Папка для результатів (за замовч.: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Перезаписати файли, навіть якщо вже існують",
    )
    args = parser.parse_args()

    ffmpeg = find_ffmpeg()
    ffprobe = find_ffprobe()

    if args.input.is_file():
        if args.input.suffix.lower() not in VIDEO_EXTENSIONS:
            print(f"Не схоже на відео: {args.input}")
            sys.exit(1)
        sources = [args.input]
    else:
        sources = collect_sources(args.input)
        if not sources:
            print(f"У папці немає відео: {args.input}")
            sys.exit(1)

    print(f"Знайдено файлів: {len(sources)}")
    print(f"Вихід: {args.output}\n")

    ok = 0
    for src in sources:
        if convert_one(
            ffmpeg,
            ffprobe,
            src,
            args.output,
            skip_existing=not args.force,
        ):
            ok += 1
        print()

    print(f"Завершено: {ok}/{len(sources)}")
    if ok < len(sources):
        sys.exit(1)


if __name__ == "__main__":
    main()
