# playlist_dl

Batch YouTube-to-ALAC downloader built for iPod. Downloads the highest-quality audio stream from YouTube and converts it to Apple Lossless (ALAC) `.m4a` — the best lossless-compatible format for iPod Classic, Nano, and the macOS Music app.

## Why ALAC?

YouTube audio tops out at ~160-256 kbps (Opus/AAC). ALAC wraps that without any further quality loss, and iPods play it natively. FLAC sounds the same but iPod doesn't support it.

## Setup

```bash
# Install dependencies (macOS)
brew install yt-dlp ffmpeg atomicparsley deno
pip install tqdm

# Keep yt-dlp current — YouTube changes constantly
yt-dlp -U
```

## Input File

Create a `songs.txt` (one YouTube URL per line):

```
# My playlist
https://www.youtube.com/watch?v=dQw4w9WgXcQ
https://youtu.be/9bZkp7q19f0
https://music.youtube.com/watch?v=kJQP7kiw5Fk

# More songs to add later
# https://www.youtube.com/watch?v=...
```

Empty lines and `#` comments are ignored.

## Usage

### Basic run

```bash
python3 playlist_dl.py
```

Reads `songs.txt`, downloads to `~/YouTubeMusicDownloads/`, sleeps 20-60s between songs, pauses every 50 for VPN rotation.

### Dry run (test without downloading)

```bash
python3 playlist_dl.py --dry-run
```

Prints the exact `yt-dlp` commands that would run — good for verifying your setup.

### Custom output directory and batch size

```bash
python3 playlist_dl.py --output-dir ~/Music/iPod --batch-size 25
```

### Use browser cookies (increases rate limit but risks account restrictions)

```bash
python3 playlist_dl.py --cookies safari
```

### Retry failed downloads

After a run, any failures are saved to `failed.txt`. Retry them:

```bash
python3 playlist_dl.py --input failed.txt
```

### Slower pace to avoid throttling

```bash
python3 playlist_dl.py --sleep-min 45 --sleep-max 90
```

## All Options

| Flag | Default | Description |
|------|---------|-------------|
| `--input` | `songs.txt` | Input file with YouTube URLs |
| `--output-dir` | `~/YouTubeMusicDownloads` | Where to save `.m4a` files |
| `--sleep-min` | `20` | Min seconds between downloads |
| `--sleep-max` | `60` | Max seconds between downloads |
| `--batch-size` | `50` | Songs per batch before VPN pause |
| `--cookies` | `none` | Browser cookies (`safari`, `chrome`, `firefox`, `none`) |
| `--js-runtime` | `deno` | JS runtime for yt-dlp |
| `--dry-run` | off | Print commands without executing |
| `--no-resume` | off | Re-download files even if they exist |

## Anti-Ban Strategy

- **ProtonVPN**: Connect before running. The script pauses between batches and prompts you to switch servers.
- **Random delays**: 20-60s between each download (configurable).
- **Batch pauses**: 5-10 minute enforced pause every 50 songs.
- **Retries**: Up to 5 attempts per song with exponential backoff.
- **Rate limiting**: Bandwidth capped at 500 KB/s.
- **Sequential only**: No parallel downloads.

For ~1,300 songs, expect 25-40 hours spread over 3-5 days (300-400/day). Run overnight in batches.

## Syncing to iPod

1. Open **Music** app (macOS) → File → Add Folder to Library → select your output folder
2. Create a playlist, drag the ALAC files in
3. Connect iPod → Sync the playlist

## Ctrl+C

Safe to interrupt at any time. The script saves unfinished URLs to `failed.txt` and prints a summary.
