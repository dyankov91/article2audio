# A2Pod 🎧

Convert any article URL into an audiobook on Apple Silicon. Share from any app → auto-converts → appears in Apple Books on all your devices.

## Features

- **Any URL** — articles, blog posts, newsletters, X/Twitter threads
- **Apple Silicon TTS** — Kokoro model via MLX, fast and natural-sounding
- **Apple Books integration** — auto-imports, syncs to iPhone via iCloud
- **Share Sheet** — queue articles from any app on iPhone or Mac
- **Background processing** — launchd job checks queue every 5 minutes
- **macOS notifications** — get notified when audiobooks are ready

## Requirements

- macOS with Apple Silicon (M1/M2/M3/M4)
- Python 3.10+
- ~500MB disk for model + dependencies

## Quick Start

```bash
git clone https://github.com/dyankov91/a2pod.git
cd a2pod
./install.sh
```

Then:

```bash
a2pod https://example.com/some-article
```

## Usage

### Direct (terminal)

```bash
# Basic — converts and opens in Books
a2pod https://example.com/article

# Custom voice (male)
a2pod https://example.com/article --voice am_michael

# Faster speech
a2pod https://example.com/article --speed 1.2

# From a local text file
a2pod --file article.txt --title "My Article"

# Generate only, don't import to Books
a2pod https://example.com/article --no-import

# Custom output path
a2pod https://example.com/article --output ~/Desktop/article.m4b
```

### Queue (from anywhere)

```bash
# Queue a link for background processing
add-to-queue https://example.com/article

# Or append directly to the queue file
echo "https://example.com/article" >> ~/Library/Mobile\ Documents/com~apple~CloudDocs/A2Pod/queue.txt
```

The queue file lives in iCloud Drive — add links from any device.

### X/Twitter

Works with tweets and threads:

```bash
a2pod https://x.com/someuser/status/1234567890
```

Uses the [bird](https://bird.fast) CLI. After install, verify auth:

```bash
bird check
```

Bird auto-detects cookies from Firefox/Chrome. If it can't find them, log into X in your browser and run `bird check` again.

## Share Sheet Setup (one-time)

Set this up to queue articles from any app with one tap:

1. Open **Shortcuts** app (Mac or iPhone)
2. Tap **+** to create a new shortcut
3. Name it **"A2Pod"**
4. Tap **"Receive input from"** at the top → select **URLs** and enable **Share Sheet**
5. Add action: **Run Shell Script**
6. Set shell to `/bin/bash` and paste:
   ```bash
   echo "$(cat)" >> "$HOME/Library/Mobile Documents/com~apple~CloudDocs/A2Pod/queue.txt"
   ```
7. Save

Now: **Share → A2Pod** from Safari, Telegram, X, Chrome, Reddit, anywhere.

Your Mac processes the queue every 5 minutes and sends a notification when done.

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
URL → Scrape (trafilatura/bird) → Chunk text → TTS (Kokoro on MLX) → M4B → Apple Books
```

1. **Scrape** — trafilatura extracts clean article text; bird handles X/Twitter
2. **Chunk** — splits into ~2000 char segments at sentence boundaries
3. **TTS** — Kokoro-82M generates audio locally on Apple Silicon
4. **Assemble** — ffmpeg concatenates chunks into M4B audiobook with metadata
5. **Import** — opens in Apple Books, syncs to iPhone via iCloud

## File Structure

```
a2pod/
├── install.sh              # One-time setup (deps, model, launchd)
├── bin/
│   ├── a2pod       # Main CLI
│   └── add-to-queue        # Queue helper
├── lib/
│   ├── extractor.py        # URL/file text extraction
│   ├── chunker.py          # Text splitting
│   ├── tts.py              # MLX Audio TTS wrapper
│   └── assembler.py        # Audio concat + M4B packaging
├── config/
│   └── com.a2pod.queue.plist  # launchd config
├── queue-processor.sh      # Background queue runner
└── README.md
```

## Output

- Audiobooks saved to `~/A2Pod/`
- Queue file at `iCloud Drive/A2Pod/queue.txt`
- Logs at `/tmp/a2pod.log`

## License

MIT
