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

# Node.js (required for bird)
if ! command -v node &>/dev/null; then
  echo "📦 Installing Node.js..."
  brew install node
else
  echo "✅ node"
fi

# bird (X/Twitter CLI)
if ! command -v bird &>/dev/null; then
  echo "📦 Installing bird (X/Twitter CLI)..."
  npm install -g @steipete/bird@0.8.0 2>/dev/null
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
pip3 install mlx-audio trafilatura soundfile "misaki[en]" phonemizer espeakng_loader --quiet

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
# ─── PATH setup ──────────────────────────────────────────────────────────────

if echo "$PATH" | tr ':' '\n' | grep -qx "$SCRIPT_DIR/bin"; then
  echo "✅ $SCRIPT_DIR/bin is already in PATH"
elif grep -q "$SCRIPT_DIR/bin" ~/.zshrc 2>/dev/null; then
  echo "✅ $SCRIPT_DIR/bin is already in ~/.zshrc (restart shell or run: source ~/.zshrc)"
else
  echo "📌 Add $SCRIPT_DIR/bin to PATH?"
  read -p "   (y/n): " add_path
  if [[ "$add_path" =~ ^[Yy]$ ]]; then
    echo "export PATH=\"$SCRIPT_DIR/bin:\$PATH\"" >> ~/.zshrc
    export PATH="$SCRIPT_DIR/bin:$PATH"
    echo "✅ Added to ~/.zshrc and current session"
  else
    echo ""
    echo "   To add later, run:"
    echo "   echo 'export PATH=\"$SCRIPT_DIR/bin:\$PATH\"' >> ~/.zshrc && source ~/.zshrc"
  fi
fi

# ─── Share Sheet shortcut ─────────────────────────────────────────────────────

echo ""
if shortcuts list 2>/dev/null | grep -qx "A2Pod"; then
  echo "✅ A2Pod shortcut already installed"
else
  echo "📌 Install 'A2Pod' Share Sheet shortcut?"
  echo "   (Share any URL → auto-queue for audio conversion)"
  read -p "   (y/n): " add_shortcut
  if [[ "$add_shortcut" =~ ^[Yy]$ ]]; then
    UNSIGNED=$(mktemp /tmp/a2a-unsigned.XXXXX)
    SIGNED=$(mktemp /tmp/a2a-signed.XXXXX.shortcut)
    plutil -convert binary1 -o "$UNSIGNED" "$SCRIPT_DIR/config/A2Pod.plist"
    shortcuts sign -m anyone -i "$UNSIGNED" -o "$SIGNED" 2>/dev/null
    open "$SIGNED"
    rm -f "$UNSIGNED"
    echo "   ⏳ Shortcuts app will open — tap 'Add Shortcut' to confirm."
    echo "   (temp file auto-cleaned on next reboot)"
  else
    echo ""
    echo "   To create manually:"
    echo "   1. Open Shortcuts app"
    echo "   2. New shortcut → name: 'A2Pod'"
    echo "   3. Set 'Receive input from' → URLs + Share Sheet"
    echo "   4. Add action: Run Shell Script"
    echo "   5. Paste:"
    echo "      echo \"\$(cat)\" >> \"\$HOME/Library/Mobile Documents/com~apple~CloudDocs/A2Pod/queue.txt\""
  fi
fi

echo ""
echo "📌 Usage:"
echo "  a2pod https://some-article.com"
echo "  add-to-queue https://some-article.com"
echo ""
