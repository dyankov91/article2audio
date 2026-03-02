# A2Pod

Convert articles into audio you can listen to anywhere. Generates natural-sounding speech locally on Apple Silicon, maintains a local podcast feed you can subscribe to from any device on your network, and optionally mirrors to S3 for public access.

```
URL / file / text  →  Extract  →  Clean  →  Summarize  →  Chunk  →  TTS  →  Intro  →  M4A  →  Local Feed
                                (regex+LLM)  (LexRank+LLM)       (Kokoro)  (jingle)       (+ VTT transcript)
                                                                                                    ↓
                                                                                              S3 (optional)
```

> **Disclaimer** — This tool is designed for personal use with content you already have access to. Respect copyright: do not redistribute generated audio unless you own the source content or have permission to do so.

## Features

- **Local-first** — no cloud accounts needed; a local HTTP server serves your podcast feed on the LAN
- **Any URL** — articles, blog posts, newsletters, X/Twitter posts and long-form articles
- **Local text** — convert `.txt` files or paste text directly (Telegram bot)
- **Episode intros** — programmatic chime jingle + spoken title before content
- **Two-pass text cleaning** — regex pass strips URLs, markdown, code, CTAs; LLM pass catches subtle patterns (parallel for cloud providers)
- **TTS pronunciation normalization** — abbreviations, numbers, currencies, symbols, and acronyms converted to spoken words
- **Extractive summarization** — LexRank selects key sentences from the full article before LLM generates a 2-3 sentence episode description
- **WebVTT transcripts** — per-chunk timestamped transcript generated alongside every audio file
- **Apple Silicon TTS** — Kokoro-82M via MLX Audio, 7 voices, parallel workers
- **Podcast feed** — RSS 2.0 with iTunes and Podcast Index extensions; subscribe once in any podcast app
- **Remote sync** — optionally mirrors to S3 (or future backends like Google Drive) for access outside your LAN
- **Telegram bot** — send URLs, paste text, or upload `.txt` files; live progress updates, inline voice/model switching
- **Deduplication** — skips URLs already in the podcast feed (override with `--force`)
- **Episode management** — delete single episodes or bulk-clear the entire feed

## Requirements

- macOS with Apple Silicon (M1/M2/M3/M4)
- Python 3.10+
- ~500 MB disk for model + dependencies
- X API bearer token (optional, for X/Twitter posts)
- AWS account (optional, for remote podcast sync)
- LLM provider (optional, for summaries and text cleaning): [Ollama](https://ollama.com) (local), [OpenAI API](https://platform.openai.com), or [Anthropic API](https://console.anthropic.com)

## Quick Start

```bash
git clone https://github.com/dyankov91/a2pod.git
cd a2pod
./install.sh
```

The installer handles dependencies, model download, PATH setup, podcast artwork, local server, and optional AWS / Telegram bot configuration. No AWS account needed — the local server starts automatically.

Then:

```bash
a2pod https://example.com/some-article
```

Your podcast feed is immediately available at `http://<your-mac>.local:8008/feed.xml`. Subscribe from any podcast app on a device connected to the same Wi-Fi.

## Usage

```bash
# Basic — converts and publishes to local podcast feed
a2pod https://example.com/article

# Custom voice
a2pod https://example.com/article --voice am_michael

# Faster speech
a2pod https://example.com/article --speed 1.2

# From a local text file
a2pod --file article.txt --title "My Article"

# Skip remote sync (local feed is always updated)
a2pod https://example.com/article --no-upload

# Custom output path
a2pod https://example.com/article --output ~/Desktop/article.m4a

# Skip summary generation
a2pod https://example.com/article --no-summary

# Skip episode intro (jingle + spoken title)
a2pod https://example.com/article --no-intro

# Reprocess a URL already in the feed
a2pod https://example.com/article --force

# Use more parallel TTS workers
a2pod https://example.com/article --workers 4

# Override LLM model
a2pod https://example.com/article --model mistral
```

### CLI Reference

| Flag | Short | Description |
|------|-------|-------------|
| `<url>` | | Article URL to convert |
| `--file` | `-f` | Local text file instead of URL |
| `--title` | `-t` | Override article title |
| `--voice` | `-v` | TTS voice (default: `af_heart`) |
| `--speed` | `-s` | Speech speed (default: `1.0`) |
| `--output` | `-o` | Custom output path |
| `--model` | `-m` | LLM model override |
| `--workers` | `-w` | Parallel TTS workers (default: `2`) |
| `--no-upload` | | Skip remote sync (local feed is always updated) |
| `--no-summary` | | Skip episode summary generation |
| `--no-intro` | | Skip episode intro (jingle + spoken title) |
| `--force` | | Reprocess even if already in the podcast feed |
| `--delete` | | Delete episode matching title or URL |
| `--delete-all` | | Delete all episodes from the feed |

### X/Twitter

Works with posts and long-form articles:

```bash
a2pod https://x.com/someuser/status/1234567890
```

Requires an X API bearer token. Add it to `~/.config/a2pod/config`:

```ini
[x]
bearer_token = YOUR_TOKEN_HERE
```

The installer can also set this up for you during `./install.sh`.

## Podcast Setup

Every article you convert is automatically added to your local podcast feed. Subscribe from any podcast app on a device connected to the same Wi-Fi network.

### Local (default)

1. Open any podcast app on your phone (Apple Podcasts, Overcast, etc.)
2. Add by URL / Subscribe to URL:
   ```
   http://<your-mac>.local:8008/feed.xml
   ```
3. Every new article you convert will appear as an episode

> **Note:** Your phone/tablet must be on the same Wi-Fi network as the Mac running the server.

### Remote via AWS S3 (optional)

For access outside your LAN, configure AWS S3 as a remote mirror during `./install.sh` or add manually:

```ini
[aws]
profile = default
bucket = my-podcast-feed
region = us-east-1
```

When configured, episodes are synced to S3 automatically after the local feed is updated. The public S3 feed URL is:
```
https://<your-bucket>.s3.<your-region>.amazonaws.com/feed.xml
```

Use `--no-upload` to skip remote sync for a single run.

### Local Server

The installer sets up a launchd service (`com.a2pod.server`) that runs automatically whenever your Mac is on.

```bash
# Check status
launchctl print gui/$(id -u)/com.a2pod.server

# Restart
launchctl kickstart -k gui/$(id -u)/com.a2pod.server

# Stop
launchctl bootout gui/$(id -u)/com.a2pod.server

# View logs
tail -f ~/.config/a2pod/server.log

# Run manually
a2pod-server
```

## Telegram Bot

Send article URLs to a Telegram bot and receive the audio file directly in chat. The bot shows live progress as each pipeline step runs.

### Setup

The installer can configure this for you during `./install.sh`. To set up manually:

1. Message [@BotFather](https://t.me/BotFather) on Telegram and create a new bot
2. Get your numeric user ID by messaging [@userinfobot](https://t.me/userinfobot)
3. Add to `~/.config/a2pod/config`:

```ini
[telegram]
bot_token = 7123456789:AAH...
allowed_users = 123456789,987654321
```

Multiple user IDs can be comma-separated. Only listed users can interact with the bot.

### Commands

| Command | Description |
|---------|-------------|
| `/start` | Introduction and feature overview |
| `/help` | Detailed usage instructions |
| `/voice` | Show or switch TTS voice (inline keyboard) |
| `/model` | Show or switch LLM provider and model (inline keyboard) |
| `/speed` | Show or set speech speed |
| `/workers` | Show or set TTS worker count |
| `/feed` | Get the podcast feed URLs (local + remote) |
| `/status` | Bot status, uptime, version, active jobs |
| `/delete` | Remove a single episode (with confirmation) |
| `/deleteall` | Remove all episodes |
| `/restart` | Restart the bot process |

### File and Text Input

Beyond URLs, the bot accepts:

- **Pasted text** — paste 50+ words directly into the chat to generate audio
- **`.txt` file uploads** — upload a text file (up to 5 MB) to convert to audio

Jobs are serialized per user — each user can run one conversion at a time.

### Running as a Background Service

The installer offers to set up a launchd service that starts the bot automatically whenever your Mac is on and restarts it if it crashes.

```bash
# Check status
launchctl print gui/$(id -u)/com.a2pod.bot

# Restart
launchctl kickstart -k gui/$(id -u)/com.a2pod.bot

# Stop
launchctl bootout gui/$(id -u)/com.a2pod.bot

# View logs
tail -f ~/.config/a2pod/bot.log

# Run manually
a2pod-bot
```

## Configuration

All configuration lives in `~/.config/a2pod/config` (INI format). The installer creates this file for you.

```ini
[podcast]
name = A2Pod                   # Podcast title in feed and episode intros

[server]
port = 8008                            # Local HTTP server port
# hostname = custom.local              # Override auto-detected hostname

[llm]
provider = ollama                      # ollama, openai, or anthropic
model = llama3.2                       # Model name for the active provider
openai_api_key = sk-...                # OpenAI API key (if using OpenAI)
anthropic_api_key = sk-ant-...         # Anthropic API key (if using Anthropic)

[tts]
voice = af_heart                       # Default TTS voice
workers = 2                            # Parallel TTS workers

[telegram]
bot_token = 7123456789:AAH...          # Telegram bot token
allowed_users = 123456789,987654321    # Comma-separated allowed user IDs

[x]
bearer_token = YOUR_TOKEN_HERE         # X/Twitter API v2 bearer token

[aws]                                  # Optional — remote sync
profile = default                      # AWS CLI profile name
bucket = my-podcast-feed               # S3 bucket name
region = us-east-1                     # AWS region
```

### LLM Providers

An LLM is used for episode summaries and the second pass of text cleaning. If no provider is configured, Ollama is used by default. If the LLM is unavailable, summaries fall back to first-sentence extraction and text cleaning uses regex only.

**Ollama (local, free):**

```ini
[llm]
provider = ollama
model = llama3.2
```

```bash
brew install ollama && ollama pull llama3.2
```

**OpenAI:**

```ini
[llm]
provider = openai
openai_api_key = sk-...
model = gpt-4o-mini
```

**Anthropic:**

```ini
[llm]
provider = anthropic
anthropic_api_key = sk-ant-...
model = claude-haiku-4-20250414
```

You can store API keys for multiple providers and switch between them at runtime via the Telegram bot's `/model` command or by editing the config. Use `--no-summary` to skip summaries entirely, or `--model <name>` to override the model for a single run.

### Voices

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

1. **Extract** — trafilatura scrapes article text from URLs; X API v2 handles X/Twitter posts; also accepts local files and pasted text
2. **Clean (regex)** — strips URLs, markdown, HTML, code blocks, CTAs, and web artifacts; normalizes abbreviations, numbers, currencies, and symbols to spoken words
3. **Summarize** — LexRank (extractive) selects key sentences across the full article, then LLM generates a 2-3 sentence episode description from those sentences
4. **Clean (LLM)** — second pass catches subtle promotional language, visual references, and awkward transitions the regex missed (runs in parallel with summarization for cloud providers)
5. **Chunk** — splits text into ~2000-character segments at sentence boundaries
6. **TTS** — Kokoro-82M generates WAV audio for each chunk in parallel (configurable worker count)
7. **Intro** — synthesizes a C-major chime jingle + spoken "[Podcast Name] presents: [Title]" + brief silence
8. **Assemble** — ffmpeg concatenates all WAVs into a single M4A with metadata; builds a WebVTT transcript with timestamps
9. **Publish** — updates local feed.xml, then syncs M4A and VTT to configured remote backends (S3, etc.)

## Project Structure

```
a2pod/
├── install.sh                 # One-time setup (deps, model, server, AWS, Telegram)
├── bin/
│   ├── a2pod          # Main CLI
│   ├── a2pod-bot      # Telegram bot entry point
│   └── a2pod-server   # Local HTTP server entry point
├── lib/
│   ├── errors.py              # Shared PipelineError exception
│   ├── pipeline.py            # Orchestration (used by CLI and bot)
│   ├── extractor.py           # URL/file/text extraction (trafilatura + X API)
│   ├── cleaner.py             # Regex + LLM two-pass text cleaning
│   ├── llm.py                 # LLM abstraction (Ollama / OpenAI / Anthropic)
│   ├── summarizer.py          # LexRank extraction + LLM episode summaries
│   ├── chunker.py             # Sentence-boundary text splitting
│   ├── tts.py                 # Kokoro-82M TTS via MLX Audio
│   ├── intro.py               # Episode intro (jingle + spoken title)
│   ├── assembler.py           # Audio concat + M4A encoding + VTT transcripts
│   ├── artwork.py             # Podcast cover image generation
│   ├── publisher.py           # Local feed management + remote backend sync
│   ├── server.py              # HTTP server for ~/A2Pod/
│   ├── telegram_bot.py        # Telegram bot handlers + polling
│   └── backends/
│       ├── __init__.py        # RemoteBackend ABC + get_configured_backends()
│       └── s3.py              # AWS S3 backend implementation
└── README.md
```

## Output

```
~/A2Pod/
├── feed.xml                       # Local podcast feed
├── artwork.jpg                    # Podcast artwork
├── Episode_Title_20260302.m4a     # Audio files
└── Episode_Title_20260302.vtt     # VTT transcripts
```

The local server serves this directory on `http://<your-mac>.local:8008/`. When AWS is configured, files are also mirrored to `s3://<your-bucket>/audiobooks/`.

## Adding a Remote Backend

To add a new remote storage provider (e.g. Google Drive):

1. Create `lib/backends/gdrive.py` implementing `RemoteBackend`
2. Add a `[gdrive]` config section
3. Register it in `backends/__init__.py`'s `get_configured_backends()`
4. No changes needed to publisher.py, pipeline.py, or any other file

## Contributing

Contributions are welcome. Please open an issue to discuss larger changes before submitting a PR.

## License

MIT
