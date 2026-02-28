#!/bin/bash
set -e

echo "🔧 A2Pod — Setup"
echo "========================"
echo ""

# Check macOS + Apple Silicon
if [[ "$(uname)" != "Darwin" ]]; then
  echo "❌ This script is designed for macOS. Exiting."
  exit 1
fi
if [[ "$(uname -m)" != "arm64" ]]; then
  echo "❌ Requires Apple Silicon (M-series). Exiting."
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# ─── Dependencies ────────────────────────────────────────────────────────────

# Homebrew
if ! command -v brew &>/dev/null; then
  echo "📦 Installing Homebrew..."
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
fi

# ffmpeg
if ! command -v ffmpeg &>/dev/null; then
  echo "📦 Installing ffmpeg..."
  brew install ffmpeg
else
  echo "✅ ffmpeg"
fi

# bird (X/Twitter CLI)
if ! command -v bird &>/dev/null; then
  echo "📦 Installing bird (X/Twitter CLI)..."
  brew install steipete/tap/bird
  echo ""
  echo "   🐦 bird installed. To authenticate:"
  echo "   1. Log into x.com in Safari or Chrome"
  echo "   2. Run: bird check"
  echo "   Bird auto-detects browser cookies. See: https://bird.fast"
  echo ""
else
  echo "✅ bird"
fi

# Python packages
echo "📦 Installing Python packages..."
pip3 install --upgrade pip --quiet
pip3 install mlx-audio trafilatura soundfile --quiet

# Pre-download Kokoro model
echo "🧠 Downloading Kokoro TTS model (~160MB)..."
python3 -c "
from mlx_audio.tts.utils import load_model
model = load_model('mlx-community/Kokoro-82M-bf16')
print('✅ Model cached')
"

# ─── Make scripts executable ─────────────────────────────────────────────────

chmod +x "$SCRIPT_DIR/bin/a2pod"
chmod +x "$SCRIPT_DIR/bin/add-to-queue"
chmod +x "$SCRIPT_DIR/queue-processor.sh"

# ─── iCloud queue folder ────────────────────────────────────────────────────

echo ""
echo "📂 Setting up iCloud queue..."
QUEUE_DIR="$HOME/Library/Mobile Documents/com~apple~CloudDocs/A2Pod"
mkdir -p "$QUEUE_DIR"
touch "$QUEUE_DIR/queue.txt"
touch "$QUEUE_DIR/done.txt"

# ─── launchd (background processor) ─────────────────────────────────────────

echo "⏰ Installing background queue processor..."
PLIST_DST="$HOME/Library/LaunchAgents/com.a2pod.queue.plist"
sed "s|INSTALL_PATH|$SCRIPT_DIR|g" "$SCRIPT_DIR/config/com.a2pod.queue.plist" > "$PLIST_DST"
launchctl unload "$PLIST_DST" 2>/dev/null || true
launchctl load "$PLIST_DST"
echo "✅ Queue processor running (checks every 5 min)"

# ─── Output directory ───────────────────────────────────────────────────────

mkdir -p "$HOME/A2Pod"

# ─── Done ────────────────────────────────────────────────────────────────────

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "✅ Setup complete!"
echo ""
echo "📌 Add to PATH (paste this):"
echo ""
echo "  echo 'export PATH=\"$SCRIPT_DIR/bin:\$PATH\"' >> ~/.zshrc && source ~/.zshrc"
echo ""
echo "📌 Create Share Sheet shortcut (one-time):"
echo ""
echo "  1. Open Shortcuts app"
echo "  2. New shortcut → name: 'A2Pod'"
echo "  3. Set 'Receive input from' → URLs + Share Sheet"
echo "  4. Add action: Run Shell Script"
echo "  5. Paste:"
echo ""
echo "     echo \"\$(cat)\" >> \"\$HOME/Library/Mobile Documents/com~apple~CloudDocs/A2Pod/queue.txt\""
echo ""
echo "  Done. Now share any link → A2Pod."
echo ""
echo "📌 Usage:"
echo "  a2pod https://some-article.com"
echo "  add-to-queue https://some-article.com"
echo ""
