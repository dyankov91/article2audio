# A2Pod 🎧

Convert any article URL into an audiobook on Apple Silicon. Generates audio locally, uploads to S3, and publishes a podcast feed you can subscribe to in Apple Podcasts on your iPhone.

## Features

- **Any URL** — articles, blog posts, newsletters, X/Twitter threads
- **Apple Silicon TTS** — Kokoro model via MLX, fast and natural-sounding
- **Podcast feed** — uploads to S3 and updates an RSS feed, subscribe once in Apple Podcasts
- **Offline fallback** — works without AWS, just saves M4B files locally

## Requirements

- macOS with Apple Silicon (M1/M2/M3/M4)
- Python 3.10+
- Node.js (for [bird](https://bird.fast) X/Twitter CLI)
- ~500MB disk for model + dependencies
- AWS account (optional, for podcast sync)

## Quick Start

```bash
git clone https://github.com/dyankov91/a2pod.git
cd a2pod
./install.sh
```

The installer handles: dependencies, model download, PATH setup, and optional AWS configuration.

Then:

```bash
a2pod https://example.com/some-article
```

## Usage

```bash
# Basic — converts and uploads to podcast feed
a2pod https://example.com/article

# Custom voice (male)
a2pod https://example.com/article --voice am_michael

# Faster speech
a2pod https://example.com/article --speed 1.2

# From a local text file
a2pod --file article.txt --title "My Article"

# Local only, skip S3 upload
a2pod https://example.com/article --no-upload

# Custom output path
a2pod https://example.com/article --output ~/Desktop/article.m4b
```

### X/Twitter

Works with tweets and threads:

```bash
a2pod https://x.com/someuser/status/1234567890
```

Uses the [bird](https://bird.fast) CLI. After install, verify auth:

```bash
bird check
```

Bird auto-detects cookies from Safari/Chrome/Firefox. If it can't find them, log into X in your browser and run `bird check` again.

## Podcast Setup

When AWS is configured, each audiobook is uploaded to S3 and the podcast feed is updated automatically. Subscribe once in Apple Podcasts:

1. Open **Apple Podcasts** on your iPhone
2. Tap **Search** → tap the search bar
3. Paste the feed URL:
   ```
   https://my-podcast-feed.s3.eu-central-1.amazonaws.com/feed.xml
   ```
4. Tap **Follow**

Every new article you convert will appear as an episode.

### AWS Setup

The installer prompts for AWS credentials. To configure manually:

```bash
aws configure --profile default
# Region: eu-central-1
```

The S3 bucket (`my-podcast-feed`) needs public read access for Apple Podcasts to fetch the feed and audio files.

## Available Voices

| Voice | Gender | ID |
|-------|--------|----|
| Heart (default) | Female | `af_heart` |
| Bella | Female | `af_bella` |
| Nicole | Female | `af_nicole` |
| Sarah | Female | `af_sarah` |
| Sky | Female | `af_sky` |
| Adam | Male | `am_adam` |
| Michael | Male | `am_michael` |

## How It Works

```
URL → Scrape (trafilatura/bird) → Chunk text → TTS (Kokoro on MLX) → M4B → S3 → Podcast Feed
```

1. **Scrape** — trafilatura extracts clean article text; bird handles X/Twitter
2. **Chunk** — splits into ~2000 char segments at sentence boundaries
3. **TTS** — Kokoro-82M generates audio locally on Apple Silicon
4. **Assemble** — ffmpeg concatenates chunks into M4B audiobook with metadata
5. **Publish** — uploads to S3 and updates the podcast RSS feed

## File Structure

```
a2pod/
├── install.sh              # One-time setup (deps, model, AWS)
├── bin/
│   └── a2pod       # Main CLI
├── lib/
│   ├── extractor.py        # URL/file text extraction
│   ├── chunker.py          # Text splitting
│   ├── tts.py              # MLX Audio TTS wrapper
│   ├── assembler.py        # Audio concat + M4B packaging
│   └── publisher.py        # S3 upload + podcast RSS feed
└── README.md
```

## Output

- Audiobooks saved to `~/A2Pod/`
- Uploaded to `s3://my-podcast-feed/audiobooks/`
- Feed at `https://my-podcast-feed.s3.eu-central-1.amazonaws.com/feed.xml`

## License

MIT
