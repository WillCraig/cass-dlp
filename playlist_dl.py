#!/usr/bin/env python3
"""playlist_dl.py -- Batch YouTube-to-ALAC downloader for iPod.

Downloads YouTube URLs as highest-quality audio, converted to Apple Lossless
Audio Codec (ALAC) in .m4a container — perfect for iPod Classic/Nano.

Usage:
    python3 playlist_dl.py
    python3 playlist_dl.py --input songs.txt --output-dir ~/Music/ALAC
    python3 playlist_dl.py --cookies safari --dry-run
    python3 playlist_dl.py --input failed.txt   # retry failed downloads

Dependencies (install once):
    brew install yt-dlp ffmpeg atomicparsley deno
    pip install tqdm
"""

import argparse
import logging
import pathlib
import random
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
import urllib.parse
from dataclasses import dataclass

try:
    from tqdm import tqdm
except ImportError:
    print("Error: tqdm is required. Install with: pip install tqdm")
    sys.exit(1)

# ── Constants ────────────────────────────────────────────────────────────────

VERSION = "1.0.0"
DEFAULT_INPUT = "songs.txt"
DEFAULT_OUTPUT_DIR = pathlib.Path.home() / "YouTubeMusicDownloads"
DEFAULT_SLEEP_MIN = 35
DEFAULT_SLEEP_MAX = 95
DEFAULT_BATCH_SIZE = 40      # ~40 songs/hour target
BATCH_PAUSE_MIN = 300        # 5 minutes
BATCH_PAUSE_MAX = 600        # 10 minutes
MAX_RETRIES = 5
BACKOFF_BASE = 2
INITIAL_DOWNLOAD_GUESS = 25  # ~7 min song at 500K + ALAC conversion

VIDEO_ID_RE = re.compile(r"\[([A-Za-z0-9_-]{11})\]\.[a-z0-9]+$")

# ── ANSI Colors ──────────────────────────────────────────────────────────────

class _Color:
    RESET  = "\033[0m"
    RED    = "\033[91m"
    GREEN  = "\033[92m"
    YELLOW = "\033[93m"
    CYAN   = "\033[96m"
    BOLD   = "\033[1m"


def _supports_color() -> bool:
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


def colored(text: str, color: str) -> str:
    if not _supports_color():
        return text
    return f"{color}{text}{_Color.RESET}"

# ── Time Estimation ──────────────────────────────────────────────────────────

def format_duration(total_seconds: float) -> str:
    """Convert seconds to a human-readable string like '2h 15m' or '45m 30s'."""
    secs = max(0, int(total_seconds))
    if secs >= 3600:
        h = secs // 3600
        m = (secs % 3600) // 60
        return f"{h}h {m:02d}m"
    elif secs >= 60:
        m = secs // 60
        s = secs % 60
        return f"{m}m {s:02d}s"
    else:
        return f"{secs}s"


def estimate_total_time(
    pending_count: int, sleep_min: int, sleep_max: int,
    batch_size: int, avg_download: float = INITIAL_DOWNLOAD_GUESS,
) -> float:
    """Estimate total seconds for pending downloads."""
    avg_sleep = (sleep_min + sleep_max) / 2
    avg_batch_pause = (BATCH_PAUSE_MIN + BATCH_PAUSE_MAX) / 2
    num_batch_pauses = max(0, (pending_count - 1) // batch_size)
    # Songs that trigger a batch pause don't also get the inter-download sleep
    num_sleeps = max(0, pending_count - 1 - num_batch_pauses)

    return (
        pending_count * avg_download
        + num_sleeps * avg_sleep
        + num_batch_pauses * avg_batch_pause
    )



# ── Logging ──────────────────────────────────────────────────────────────────

class _ColoredFormatter(logging.Formatter):
    LEVEL_COLORS = {
        logging.WARNING: _Color.YELLOW,
        logging.ERROR: _Color.RED,
        logging.CRITICAL: _Color.RED,
    }

    def format(self, record: logging.LogRecord) -> str:
        msg = super().format(record)
        if _supports_color():
            color = self.LEVEL_COLORS.get(record.levelno)
            if color:
                msg = f"{color}{msg}{_Color.RESET}"
        return msg


def setup_logging(log_file: pathlib.Path) -> logging.Logger:
    logger = logging.getLogger("playlist_dl")
    logger.setLevel(logging.DEBUG)

    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)-8s %(message)s"))
    logger.addHandler(fh)

    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(_ColoredFormatter("%(message)s"))
    logger.addHandler(ch)

    return logger

# ── CLI Arguments ────────────────────────────────────────────────────────────

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Batch YouTube -> ALAC (.m4a) downloader for iPod.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="To retry failed downloads: python3 playlist_dl.py --input failed.txt",
    )
    p.add_argument("--input", default=DEFAULT_INPUT,
                   help=f"Input file with YouTube URLs (default: {DEFAULT_INPUT})")
    p.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR),
                   help=f"Output directory (default: {DEFAULT_OUTPUT_DIR})")
    p.add_argument("--sleep-min", type=int, default=DEFAULT_SLEEP_MIN,
                   help=f"Min seconds between downloads (default: {DEFAULT_SLEEP_MIN})")
    p.add_argument("--sleep-max", type=int, default=DEFAULT_SLEEP_MAX,
                   help=f"Max seconds between downloads (default: {DEFAULT_SLEEP_MAX})")
    p.add_argument("--batch-pause", action="store_true", default=False,
                   help="Pause every --batch-size downloads for VPN rotation (off by default)")
    p.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE,
                   help=f"Downloads per batch when --batch-pause is enabled (default: {DEFAULT_BATCH_SIZE})")
    p.add_argument("--cookies", choices=["safari", "chrome", "firefox", "none"],
                   default="none",
                   help="Browser to extract cookies from (default: none)")
    p.add_argument("--js-runtime", default="deno",
                   help="JS runtime for yt-dlp (default: deno)")
    p.add_argument("--max-downloads", type=int, default=0,
                   help="Stop after N downloads (0 = unlimited, default: 0)")
    p.add_argument("--dry-run", action="store_true",
                   help="Print yt-dlp commands without executing")
    p.add_argument("--resume", action="store_true", default=True,
                   help="Skip already-downloaded files (default: on)")
    p.add_argument("--no-resume", action="store_false", dest="resume",
                   help="Re-download even if file exists")

    args = p.parse_args(argv)

    if args.sleep_min > args.sleep_max:
        p.error("--sleep-min must be <= --sleep-max")
    if args.batch_size < 1:
        p.error("--batch-size must be >= 1")
    if args.max_downloads < 0:
        p.error("--max-downloads must be >= 0")
    if not pathlib.Path(args.input).is_file():
        p.error(f"Input file not found: {args.input}")

    return args

# ── Dependency Check ─────────────────────────────────────────────────────────

def check_all_dependencies() -> None:
    deps = {
        "yt-dlp": "brew install yt-dlp && yt-dlp -U",
        "ffmpeg": "brew install ffmpeg",
    }
    ap = shutil.which("atomicparsley") or shutil.which("AtomicParsley")

    missing = []
    for name, hint in deps.items():
        if not shutil.which(name):
            missing.append((name, hint))
    if not ap:
        missing.append(("AtomicParsley", "brew install atomicparsley"))

    if missing:
        print(colored("Missing required dependencies:", _Color.RED))
        for name, hint in missing:
            print(f"  {name}: {hint}")
        sys.exit(1)

# ── Banner ───────────────────────────────────────────────────────────────────

def print_banner(args: argparse.Namespace, pending_count: int = 0) -> None:
    print()
    print(colored("=" * 60, _Color.CYAN))
    print(colored(f"  playlist_dl v{VERSION} -- YouTube -> ALAC for iPod", _Color.BOLD))
    print(colored("=" * 60, _Color.CYAN))
    print()
    print(colored("  WARNING: ProtonVPN MUST be connected before running!", _Color.YELLOW))
    if args.cookies != "none":
        print(colored(f"  WARNING: Using cookies from {args.cookies} -- this risks", _Color.YELLOW))
        print(colored("           YouTube account restrictions. Use at own risk!", _Color.YELLOW))
    print(colored("  NOTE: This tool is for personal offline use only.", _Color.YELLOW))
    print()
    print(f"  Input:      {args.input}")
    print(f"  Output:     {args.output_dir}")
    if args.batch_pause:
        print(f"  Batch size: {args.batch_size} (pause enabled)")
    print(f"  Max DLs:    {args.max_downloads or 'unlimited'}")
    print(f"  Sleep:      {args.sleep_min}-{args.sleep_max}s")
    print(f"  Cookies:    {args.cookies}")
    print(f"  JS runtime: {args.js_runtime}")
    print(f"  Dry run:    {args.dry_run}")
    print(f"  Resume:     {args.resume}")
    if pending_count > 0 and not args.dry_run:
        effective = min(pending_count, args.max_downloads) if args.max_downloads > 0 else pending_count
        batch_sz = args.batch_size if args.batch_pause else effective + 1
        est = estimate_total_time(
            effective, args.sleep_min, args.sleep_max, batch_sz
        )
        print()
        print(colored(
            f"  Estimated time: ~{format_duration(est)} for {effective} song(s)"
            f"  (refines as downloads progress)",
            _Color.CYAN,
        ))
    print()

# ── Input Parsing ────────────────────────────────────────────────────────────

AGE_RESTRICT_PATTERNS = (
    "Sign in to confirm your age",
    "age-restricted",
    "age_restricted",
    "This video may be inappropriate for some users",
    "confirm your age",
)


@dataclass
class DownloadTask:
    url: str
    video_id: str
    index: int
    status: str = "pending"  # pending | skipped | downloaded | failed | age_restricted
    download_seconds: float = 0.0


def extract_video_id(url: str) -> str | None:
    parsed = urllib.parse.urlparse(url)
    if parsed.hostname in ("youtu.be",):
        vid = parsed.path.lstrip("/")[:11]
        if re.fullmatch(r"[A-Za-z0-9_-]{11}", vid):
            return vid
    elif parsed.hostname in ("www.youtube.com", "youtube.com", "music.youtube.com"):
        qs = urllib.parse.parse_qs(parsed.query)
        v = qs.get("v", [None])[0]
        if v and re.fullmatch(r"[A-Za-z0-9_-]{11}", v):
            return v
    return None


def is_playlist_url(url: str) -> bool:
    """Check if a URL is a YouTube playlist (not an individual video)."""
    parsed = urllib.parse.urlparse(url)
    if parsed.hostname not in ("www.youtube.com", "youtube.com", "music.youtube.com"):
        return False
    qs = urllib.parse.parse_qs(parsed.query)
    # It's a playlist if it has a "list" param and either no "v" param
    # or the path is /playlist
    return "list" in qs and (parsed.path == "/playlist" or "v" not in qs)


def expand_playlist(url: str, logger: logging.Logger) -> list[str]:
    """Use yt-dlp --flat-playlist to extract individual video URLs from a playlist."""
    logger.info(f"Expanding playlist: {url}")
    result = subprocess.run(
        ["yt-dlp", "--flat-playlist", "--print", "url", url],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        logger.error(f"  Failed to expand playlist: {stderr}")
        return []
    urls = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    logger.info(f"  Found {len(urls)} video(s) in playlist")
    return urls


def parse_input_file(filepath: pathlib.Path, logger: logging.Logger) -> list[DownloadTask]:
    tasks: list[DownloadTask] = []
    task_index = 0

    with open(filepath, "r", encoding="utf-8") as f:
        for lineno, raw_line in enumerate(f, start=1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue

            # Expand playlist URLs into individual video URLs
            if is_playlist_url(line):
                video_urls = expand_playlist(line, logger)
                for vurl in video_urls:
                    vid = extract_video_id(vurl)
                    if vid:
                        task_index += 1
                        tasks.append(DownloadTask(url=vurl, video_id=vid, index=task_index))
                    else:
                        logger.warning(f"  Playlist entry: skipping invalid URL: {vurl}")
                continue

            vid = extract_video_id(line)
            if not vid:
                logger.warning(f"Line {lineno}: skipping invalid URL: {line}")
                continue
            task_index += 1
            tasks.append(DownloadTask(url=line, video_id=vid, index=task_index))
    return tasks

# ── Resume ───────────────────────────────────────────────────────────────────

def scan_existing_downloads(output_dir: pathlib.Path) -> set[str]:
    if not output_dir.is_dir():
        return set()
    existing_ids: set[str] = set()
    for f in output_dir.iterdir():
        if f.is_file():
            m = VIDEO_ID_RE.search(f.name)
            if m:
                existing_ids.add(m.group(1))
    return existing_ids


def apply_resume(
    tasks: list[DownloadTask], existing_ids: set[str], logger: logging.Logger
) -> int:
    skipped = 0
    for task in tasks:
        if task.video_id in existing_ids:
            task.status = "skipped"
            skipped += 1
    if skipped:
        logger.info(f"Resume: skipping {skipped} already-downloaded song(s)")
    return skipped

# ── yt-dlp Command Builder ──────────────────────────────────────────────────

def build_ytdlp_command(
    url: str,
    output_dir: pathlib.Path,
    cookies: str,
    js_runtime: str,
) -> list[str]:
    output_template = str(output_dir / "%(artist)s - %(title)s [%(id)s].%(ext)s")

    cmd = [
        "yt-dlp",
        "-f", "bestaudio/best",
        "-x", "--audio-format", "alac",
        "--embed-metadata",
        "--embed-thumbnail",
        "--add-metadata",
        "--parse-metadata", "title:%(track)s",
        "--parse-metadata", "uploader:%(artist)s",
        "-o", output_template,
        "--no-overwrites",
        "--continue",
        "--retries", "3",
        "--limit-rate", "500K",
    ]

    if cookies != "none":
        cmd.extend(["--cookies-from-browser", cookies])

    cmd.extend(["--js-runtimes", js_runtime])
    cmd.append(url)

    return cmd

# ── Single Download Executor ─────────────────────────────────────────────────

def execute_download(
    task: DownloadTask,
    output_dir: pathlib.Path,
    cookies: str,
    js_runtime: str,
    dry_run: bool,
    logger: logging.Logger,
) -> bool:
    cmd = build_ytdlp_command(task.url, output_dir, cookies, js_runtime)

    if dry_run:
        logger.info(f"[DRY RUN] {shlex.join(cmd)}")
        task.status = "downloaded"
        return True

    for attempt in range(MAX_RETRIES):
        logger.info(
            f"Downloading [{task.index}]: {task.url}"
            + (f" (attempt {attempt + 1}/{MAX_RETRIES})" if attempt > 0 else "")
        )

        t_start = time.monotonic()
        result = subprocess.run(cmd, capture_output=True, text=True)
        elapsed = time.monotonic() - t_start

        if result.returncode == 0:
            task.download_seconds = elapsed
            title_line = ""
            for line in result.stdout.splitlines():
                if "Destination:" in line or "[ExtractAudio]" in line:
                    title_line = line.strip()
                    break
            logger.info(colored(f"  OK ({elapsed:.0f}s): {title_line or task.url}", _Color.GREEN))
            task.status = "downloaded"
            return True

        stderr_text = (result.stderr or "").strip()
        stderr_lines = stderr_text.splitlines()
        last_lines = "\n    ".join(stderr_lines[-3:]) if stderr_lines else "(no stderr)"
        logger.warning(f"  Failed (rc={result.returncode}):\n    {last_lines}")

        # Detect age-restricted videos — no point retrying without cookies
        if any(pat in stderr_text for pat in AGE_RESTRICT_PATTERNS):
            task.status = "age_restricted"
            logger.warning(colored(
                f"  AGE-RESTRICTED: {task.url}\n"
                f"    Skipping retries. Rerun with --cookies to download.",
                _Color.YELLOW,
            ))
            return False

        if attempt < MAX_RETRIES - 1:
            backoff = (BACKOFF_BASE ** (attempt + 1)) + random.uniform(0, 5)
            logger.info(f"  Retrying in {backoff:.0f}s...")
            time.sleep(backoff)

    task.status = "failed"
    logger.error(colored(f"  FAILED after {MAX_RETRIES} attempts: {task.url}", _Color.RED))
    return False

# ── Failed URL File ──────────────────────────────────────────────────────────

def write_failed_file(
    tasks: list[DownloadTask], failed_file: pathlib.Path, logger: logging.Logger
) -> None:
    failed_urls = [t.url for t in tasks if t.status == "failed"]
    if not failed_urls:
        return
    with open(failed_file, "w", encoding="utf-8") as f:
        for url in failed_urls:
            f.write(url + "\n")
    logger.info(f"Failed URLs saved to: {failed_file}")


def write_age_restricted_file(
    tasks: list[DownloadTask], ar_file: pathlib.Path, logger: logging.Logger
) -> None:
    ar_urls = [t.url for t in tasks if t.status == "age_restricted"]
    if not ar_urls:
        return
    with open(ar_file, "w", encoding="utf-8") as f:
        f.write("# Age-restricted videos — rerun with --cookies to download:\n")
        f.write("# python3 playlist_dl.py --input age_restricted.txt --cookies safari\n\n")
        for url in ar_urls:
            f.write(url + "\n")
    logger.info(f"Age-restricted URLs saved to: {ar_file}")

# ── Batch Pause ──────────────────────────────────────────────────────────────

def batch_pause(batch_num: int, logger: logging.Logger) -> None:
    pause_time = random.uniform(BATCH_PAUSE_MIN, BATCH_PAUSE_MAX)
    mins = pause_time / 60

    print()
    print(colored("=" * 60, _Color.CYAN))
    print(colored(f"  Batch {batch_num} complete!", _Color.GREEN))
    print(colored(f"  Pausing for {mins:.1f} minutes...", _Color.CYAN))
    print(colored("  Recommended: switch ProtonVPN server now.", _Color.YELLOW))
    print(colored("=" * 60, _Color.CYAN))
    print()

    time.sleep(pause_time)
    input(colored("  Press Enter to continue...", _Color.CYAN))
    print()

# ── Download Stats ───────────────────────────────────────────────────────────

@dataclass
class DownloadStats:
    downloaded: int = 0
    skipped: int = 0
    failed: int = 0
    age_restricted: int = 0
    total: int = 0

# ── Main Download Loop ──────────────────────────────────────────────────────

def run_download_loop(
    tasks: list[DownloadTask],
    args: argparse.Namespace,
    logger: logging.Logger,
) -> DownloadStats:
    stats = DownloadStats(total=len(tasks))

    pending = [t for t in tasks if t.status == "pending"]
    stats.skipped = len(tasks) - len(pending)

    # Apply --max-downloads limit
    max_dl = args.max_downloads
    if max_dl > 0 and len(pending) > max_dl:
        logger.info(f"Limiting to {max_dl} downloads (--max-downloads)")
        pending = pending[:max_dl]

    if not pending:
        logger.info("All URLs already downloaded -- nothing to do.")
        return stats

    output_dir = pathlib.Path(args.output_dir)
    batch_counter = 0
    batch_num = 1
    download_times: list[float] = []

    # Update signal handler stats reference
    _interrupt_state["stats"] = stats

    # Use a large batch_size for estimation when pauses are disabled
    est_batch = args.batch_size if args.batch_pause else len(pending) + 1

    with tqdm(
        total=len(pending),
        desc="Downloading",
        unit="song",
        bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}] {postfix}",
    ) as pbar:
        # Show initial ETA
        initial_eta = estimate_total_time(
            len(pending), args.sleep_min, args.sleep_max, est_batch
        )
        pbar.set_postfix_str(f"ETA: ~{format_duration(initial_eta)}")

        for i, task in enumerate(pending):
            success = execute_download(
                task, output_dir, args.cookies, args.js_runtime, args.dry_run, logger
            )
            if success:
                stats.downloaded += 1
                if task.download_seconds > 0:
                    download_times.append(task.download_seconds)
            elif task.status == "age_restricted":
                stats.age_restricted += 1
            else:
                stats.failed += 1

            pbar.update(1)
            batch_counter += 1

            # Update live ETA
            remaining = len(pending) - i - 1
            if remaining > 0:
                avg_dl = sum(download_times) / len(download_times) if download_times else INITIAL_DOWNLOAD_GUESS
                eta = estimate_total_time(
                    remaining, args.sleep_min, args.sleep_max, est_batch,
                    avg_download=avg_dl,
                )
                pbar.set_postfix_str(f"ETA: ~{format_duration(eta)}")
            else:
                pbar.set_postfix_str("Done!")

            is_last = i >= len(pending) - 1

            if args.batch_pause and batch_counter >= args.batch_size and not is_last:
                batch_pause(batch_num, logger)
                batch_num += 1
                batch_counter = 0
            elif not is_last and not args.dry_run:
                sleep_time = random.uniform(args.sleep_min, args.sleep_max)
                logger.info(f"Sleeping {sleep_time:.0f}s before next download...")
                time.sleep(sleep_time)

    return stats

# ── Summary ──────────────────────────────────────────────────────────────────

def print_summary(stats: DownloadStats, logger: logging.Logger) -> None:
    print()
    print(colored("=" * 60, _Color.CYAN))
    print(colored("  Download Summary", _Color.BOLD))
    print(colored("=" * 60, _Color.CYAN))
    print(colored(f"  Downloaded:      {stats.downloaded}", _Color.GREEN))
    print(colored(f"  Skipped:         {stats.skipped}", _Color.YELLOW))
    print(colored(f"  Age-restricted:  {stats.age_restricted}", _Color.YELLOW))
    print(colored(f"  Failed:          {stats.failed}", _Color.RED))
    print(f"  Total:           {stats.total}")
    print()
    if stats.age_restricted > 0:
        print(colored(
            "  To download age-restricted videos:\n"
            "    python3 playlist_dl.py --input age_restricted.txt --cookies safari",
            _Color.YELLOW,
        ))
    if stats.failed > 0:
        print(colored(
            "  To retry failed downloads:\n"
            "    python3 playlist_dl.py --input failed.txt",
            _Color.YELLOW,
        ))
    if stats.age_restricted > 0 or stats.failed > 0:
        print()

# ── Signal Handling ──────────────────────────────────────────────────────────

_interrupt_state: dict = {}


def _signal_handler(signum: int, frame) -> None:
    print()
    logger = _interrupt_state.get("logger")
    tasks = _interrupt_state.get("tasks", [])
    stats = _interrupt_state.get("stats")

    if logger:
        logger.info(colored("Interrupted! Saving progress...", _Color.YELLOW))

    for t in tasks:
        if t.status == "pending":
            t.status = "failed"

    if logger:
        input_path = _interrupt_state.get("input_path")
        if input_path:
            parent = pathlib.Path(input_path).parent
            write_failed_file(tasks, parent / "failed.txt", logger)
            write_age_restricted_file(tasks, parent / "age_restricted.txt", logger)

    if stats and logger:
        print_summary(stats, logger)

    sys.exit(130)


def setup_signal_handler(
    stats: DownloadStats,
    tasks: list[DownloadTask],
    logger: logging.Logger,
    input_path: str,
) -> None:
    _interrupt_state["stats"] = stats
    _interrupt_state["tasks"] = tasks
    _interrupt_state["logger"] = logger
    _interrupt_state["input_path"] = input_path
    signal.signal(signal.SIGINT, _signal_handler)

# ── Main ─────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    output_dir = pathlib.Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    args.output_dir = str(output_dir)

    logger = setup_logging(output_dir / "download.log")

    check_all_dependencies()

    input_path = pathlib.Path(args.input)
    tasks = parse_input_file(input_path, logger)
    if not tasks:
        logger.error("No valid YouTube URLs found in input file.")
        return 1

    logger.info(f"Loaded {len(tasks)} URL(s) from {args.input}")

    if args.resume:
        existing_ids = scan_existing_downloads(output_dir)
        apply_resume(tasks, existing_ids, logger)

    pending_count = sum(1 for t in tasks if t.status == "pending")
    print_banner(args, pending_count)

    stats = DownloadStats(total=len(tasks))
    setup_signal_handler(stats, tasks, logger, args.input)

    stats = run_download_loop(tasks, args, logger)

    _interrupt_state["stats"] = stats

    failed_file = input_path.parent / "failed.txt"
    write_failed_file(tasks, failed_file, logger)

    ar_file = input_path.parent / "age_restricted.txt"
    write_age_restricted_file(tasks, ar_file, logger)

    print_summary(stats, logger)
    return 1 if stats.failed > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
