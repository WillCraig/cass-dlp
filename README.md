# cass-dlp

ytdlp wrapper to space out downloads. perfect output for an ipod classic. for Cassie.

## Why ALAC?

For the ipod

## Setup

```bash
# Install dependencies (macOS)
brew install yt-dlp ffmpeg atomicparsley deno
pip install tqdm

# Keep yt-dlp current — YouTube changes constantly
yt-dlp -U
```

## Input File

Create a `songs.txt` with YouTube URLs and/or playlist links (one per line):

```
# Individual songs
https://www.youtube.com/watch?v=dQw4w9WgXcQ
https://youtu.be/9bZkp7q19f0
https://music.youtube.com/watch?v=kJQP7kiw5Fk

# Full playlists (auto-expanded into individual videos)
https://www.youtube.com/playlist?list=PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf

# More songs to add later
# https://www.youtube.com/watch?v=...
```

Empty lines and `#` comments are ignored. Playlist URLs are automatically expanded into their individual videos before downloading.

## Usage

### Basic run

```bash
python3 playlist_dl.py
```

Reads `songs.txt`, downloads to `~/YouTubeMusicDownloads/`, sleeps 35-95s between songs. Targets ~40 downloads/hour.

### Download a set number and pick up tomorrow

```bash
python3 playlist_dl.py --max-downloads 50
```

Downloads 50 songs then exits cleanly. Run the same command the next day — resume automatically skips what's already downloaded and picks up where you left off.

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
python3 playlist_dl.py --sleep-min 45 --sleep-max 120
```

### Typical multi-day workflow for a large library

```bash
# Day 1: download first 300
python3 playlist_dl.py --max-downloads 300

# Day 2: picks up at 301 automatically
python3 playlist_dl.py --max-downloads 300

# Day 3: finish the rest
python3 playlist_dl.py

# Retry any that failed along the way
python3 playlist_dl.py --input failed.txt
```

## All Options

| Flag | Default | Description |
|------|---------|-------------|
| `--input` | `songs.txt` | Input file with YouTube URLs or playlist links |
| `--output-dir` | `~/YouTubeMusicDownloads` | Where to save `.m4a` files |
| `--sleep-min` | `35` | Min seconds between downloads |
| `--sleep-max` | `95` | Max seconds between downloads |
| `--batch-size` | `40` | Songs per batch before VPN pause |
| `--max-downloads` | `0` (unlimited) | Stop after N downloads |
| `--cookies` | `none` | Browser cookies (`safari`, `chrome`, `firefox`, `none`) |
| `--js-runtime` | `deno` | JS runtime for yt-dlp |
| `--dry-run` | off | Print commands without executing |
| `--no-resume` | off | Re-download files even if they exist |

## Time Estimates

The script shows an estimated completion time before starting, and a live ETA that refines as it learns actual download speeds. At default settings (~40 songs/hour):

| Songs | Estimated Time |
|-------|---------------|
| 40 | ~1 hour |
| 300 | ~8 hours |
| 1,300 | ~36 hours |

## Ctrl+C

Safe to interrupt at any time. The script saves unfinished URLs to `failed.txt` and prints a summary. Resume picks up where you left off next run.
