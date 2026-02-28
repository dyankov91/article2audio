# A2Pod 🎧

Convert any article URL into an audiobook on Apple Silicon. Generates audio locally, uploads to S3, and publishes a podcast feed you can subscribe to in Apple Podcasts on your iPhone.

## Features

- **Any URL** — articles, blog posts, newsletters, X/Twitter threads
- **Text cleaning** — automatically strips URLs, markdown, code blocks, CTAs, and web artifacts before TTS
- **Episode summaries** — generates 2-3 sentence descriptions via Ollama (optional, graceful fallback)
- **Apple Silicon TTS** — Kokoro model via MLX, fast and natural-sounding
- **Podcast feed** — uploads to S3 and updates an RSS feed, subscribe once in Apple Podcasts
- **Offline fallback** — works without AWS, just saves M4B files locally

## Requirements

- macOS with Apple Silicon (M1/M2/M3/M4)
- Python 3.10+
- Node.js (for [bird](https://bird.fast) X/Twitter CLI)
- ~500MB disk for model + dependencies
- AWS account (optional, for podcast sync)
- [Ollama](https://ollama.com) (optional, for episode summaries)

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

# Skip summary generation
a2pod https://example.com/article --no-summary

# Use a different Ollama model for summaries
a2pod https://example.com/article --model mistral
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

## Episode Summaries

Summaries are generated automatically via a local Ollama model and added to the podcast feed as episode descriptions (visible in Apple Podcasts).

To set up:

```bash
# Install Ollama
brew install ollama

# Pull a model (default: llama3.2)
ollama pull llama3.2
```

If Ollama isn't running, a fallback summary (first sentence of the article) is used instead. Use `--no-summary` to skip entirely, or `--model <name>` to use a different model.

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
URL → Scrape → Clean → Summarize → Chunk → TTS → M4A → S3 → Podcast Feed
```

1. **Scrape** — trafilatura extracts article text; bird handles X/Twitter
2. **Clean** — regex strips URLs, markdown, code blocks, CTAs, and web artifacts
3. **Summarize** — Ollama generates a 2-3 sentence episode description (optional)
4. **Chunk** — splits into ~2000 char segments at sentence boundaries
5. **TTS** — Kokoro-82M generates audio locally on Apple Silicon
6. **Assemble** — ffmpeg concatenates chunks into M4A with metadata
7. **Publish** — uploads to S3 and updates the podcast RSS feed

## File Structure

```
a2pod/
├── install.sh              # One-time setup (deps, model, AWS)
├── bin/
│   └── a2pod       # Main CLI
├── lib/
│   ├── extractor.py        # URL/file text extraction
│   ├── cleaner.py          # Regex text cleaning for audio
│   ├── summarizer.py       # Ollama episode summaries
│   ├── chunker.py          # Text splitting
│   ├── tts.py              # MLX Audio TTS wrapper
│   ├── assembler.py        # Audio concat + M4A packaging
│   └── publisher.py        # S3 upload + podcast RSS feed
└── README.md
```

## Output

- Audiobooks saved to `~/A2Pod/`
- Uploaded to `s3://my-podcast-feed/audiobooks/`
- Feed at `https://my-podcast-feed.s3.eu-central-1.amazonaws.com/feed.xml`

## License

MIT
