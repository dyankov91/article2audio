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
CONFIG_DIR="$HOME/.config/a2pod"

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

# Python packages
echo "📦 Installing Python packages..."
pip3 install --upgrade pip --quiet
pip3 install mlx-audio trafilatura soundfile "misaki[en]" phonemizer espeakng_loader boto3 mutagen Pillow python-telegram-bot --quiet

# Pre-download Kokoro model
echo "🧠 Downloading Kokoro TTS model (~160MB)..."
python3 -c "
from mlx_audio.tts.utils import load_model
model = load_model('mlx-community/Kokoro-82M-bf16')
print('✅ Model cached')
"

# ─── Make scripts executable ─────────────────────────────────────────────────

chmod +x "$SCRIPT_DIR/bin/a2pod"
chmod +x "$SCRIPT_DIR/bin/a2pod-bot"

# ─── Output directory ───────────────────────────────────────────────────────

mkdir -p "$HOME/A2Pod"

# ─── PATH setup ──────────────────────────────────────────────────────────────

echo ""
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

# ─── Optional: X API for posts ────────────────────────────────────────────────

echo ""
echo "🐦 Optional: Enable X (Twitter) post support?"
echo "   Lets you convert x.com posts and articles to audio."
echo ""

EXISTING_X_TOKEN=""
if [[ -f "$CONFIG_DIR/config" ]]; then
  EXISTING_X_TOKEN=$(python3 -c "
import configparser, os
cfg = configparser.ConfigParser()
cfg.read(os.path.expanduser('~/.config/a2pod/config'))
print(cfg.get('x', 'bearer_token', fallback=''))
" 2>/dev/null)
fi

if [[ -n "$EXISTING_X_TOKEN" ]]; then
  echo "✅ X API bearer token already configured"
else
  read -p "   Add X API bearer token? (y/n): " setup_x
  if [[ "$setup_x" =~ ^[Yy]$ ]]; then
    echo ""
    read -s -p "   Bearer token: " X_TOKEN
    echo ""

    mkdir -p "$CONFIG_DIR"
    # Add [x] section to config (preserve existing sections)
    python3 -c "
import configparser, os
path = os.path.expanduser('~/.config/a2pod/config')
cfg = configparser.ConfigParser()
cfg.read(path)
if not cfg.has_section('x'):
    cfg.add_section('x')
cfg.set('x', 'bearer_token', '$X_TOKEN')
with open(path, 'w') as f:
    cfg.write(f)
"
    echo "   ✅ X API token saved to $CONFIG_DIR/config"
  else
    echo "   Skipped. Add later to $CONFIG_DIR/config:"
    echo "   [x]"
    echo "   bearer_token = YOUR_TOKEN"
  fi
fi

# ─── LLM Provider for summaries and text cleaning ────────────────────────────

echo ""
echo "📝 LLM Provider for summaries and text cleaning:"
echo "   Generates episode descriptions and cleans text before TTS."
echo ""
echo "   1) Ollama (local, free, requires ~2GB disk)"
echo "   2) OpenAI API (cloud, requires API key)"
echo "   3) Anthropic API (cloud, requires API key)"
echo "   4) Skip (summaries will use fallback)"
echo ""
read -p "   Choose (1-4) [1]: " llm_choice
llm_choice="${llm_choice:-1}"

case "$llm_choice" in
  1)
    # Ollama setup
    if command -v ollama &>/dev/null; then
      echo "✅ Ollama is installed"
      if ollama list 2>/dev/null | grep -q "llama3.2"; then
        echo "✅ llama3.2 model is available"
      else
        read -p "   Pull llama3.2 model (~2GB)? (y/n): " pull_model
        if [[ "$pull_model" =~ ^[Yy]$ ]]; then
          if ! curl -sf http://localhost:11434/api/tags &>/dev/null; then
            echo "   Starting Ollama server..."
            brew services start ollama
            sleep 2
          fi
          echo "   Downloading llama3.2..."
          ollama pull llama3.2
          echo "   ✅ Model ready"
        else
          echo "   Skipped. Use --no-summary or pull a model later: ollama pull llama3.2"
        fi
      fi
    else
      read -p "   Install Ollama? (y/n): " install_ollama
      if [[ "$install_ollama" =~ ^[Yy]$ ]]; then
        echo "   Installing Ollama..."
        brew install ollama
        echo "   Starting Ollama server..."
        brew services start ollama
        sleep 2
        echo "   Pulling llama3.2 model (~2GB)..."
        ollama pull llama3.2
        echo "   ✅ Ollama ready (running as background service)"
      else
        echo "   Skipped. Summaries will use fallback (first sentence)."
        echo "   Install later: brew install ollama && ollama pull llama3.2"
      fi
    fi

    # Write [llm] section to config
    mkdir -p "$CONFIG_DIR"
    python3 -c "
import configparser, os
path = os.path.expanduser('~/.config/a2pod/config')
cfg = configparser.ConfigParser()
cfg.read(path)
if not cfg.has_section('llm'):
    cfg.add_section('llm')
cfg.set('llm', 'provider', 'ollama')
with open(path, 'w') as f:
    cfg.write(f)
"
    ;;
  2)
    # OpenAI setup
    echo ""
    read -s -p "   OpenAI API key: " OPENAI_KEY
    echo ""

    echo "   Installing openai package..."
    pip3 install openai --quiet

    mkdir -p "$CONFIG_DIR"
    python3 -c "
import configparser, os
path = os.path.expanduser('~/.config/a2pod/config')
cfg = configparser.ConfigParser()
cfg.read(path)
if not cfg.has_section('llm'):
    cfg.add_section('llm')
cfg.set('llm', 'provider', 'openai')
cfg.set('llm', 'api_key', '$OPENAI_KEY')
cfg.set('llm', 'model', 'gpt-4o-mini')
with open(path, 'w') as f:
    cfg.write(f)
"
    echo "   ✅ OpenAI configured (model: gpt-4o-mini)"
    ;;
  3)
    # Anthropic setup
    echo ""
    read -s -p "   Anthropic API key: " ANTHROPIC_KEY
    echo ""

    echo "   Installing anthropic package..."
    pip3 install anthropic --quiet

    mkdir -p "$CONFIG_DIR"
    python3 -c "
import configparser, os
path = os.path.expanduser('~/.config/a2pod/config')
cfg = configparser.ConfigParser()
cfg.read(path)
if not cfg.has_section('llm'):
    cfg.add_section('llm')
cfg.set('llm', 'provider', 'anthropic')
cfg.set('llm', 'api_key', '$ANTHROPIC_KEY')
cfg.set('llm', 'model', 'claude-haiku-4-20250414')
with open(path, 'w') as f:
    cfg.write(f)
"
    echo "   ✅ Anthropic configured (model: claude-haiku-4-20250414)"
    ;;
  4)
    echo "   Skipped. Summaries will use fallback (first sentence)."
    echo "   Configure later in ~/.config/a2pod/config under [llm]."
    ;;
  *)
    echo "   Invalid choice. Skipping LLM setup."
    ;;
esac

# ─── Optional: AWS / Podcast sync ────────────────────────────────────────────

echo ""
echo "📡 Optional: Enable podcast sync via S3?"
echo "   Audiobooks upload to S3 and appear in Apple Podcasts on iPhone."
echo ""

# Check if already configured
if [[ -f "$CONFIG_DIR/config" ]] && python3 -c "
import configparser, os
cfg = configparser.ConfigParser()
cfg.read(os.path.expanduser('~/.config/a2pod/config'))
if 'aws' in cfg and cfg['aws'].get('profile') and cfg['aws'].get('bucket'):
    import boto3
    s = boto3.Session(profile_name=cfg['aws']['profile'])
    assert s.get_credentials()
" 2>/dev/null; then
  EXISTING_PROFILE=$(python3 -c "
import configparser, os
cfg = configparser.ConfigParser()
cfg.read(os.path.expanduser('~/.config/a2pod/config'))
print(cfg['aws']['profile'])
")
  EXISTING_BUCKET=$(python3 -c "
import configparser, os
cfg = configparser.ConfigParser()
cfg.read(os.path.expanduser('~/.config/a2pod/config'))
print(cfg['aws']['bucket'])
")
  EXISTING_REGION=$(python3 -c "
import configparser, os
cfg = configparser.ConfigParser()
cfg.read(os.path.expanduser('~/.config/a2pod/config'))
print(cfg['aws']['region'])
")
  EXISTING_NAME=$(python3 -c "
import configparser, os
cfg = configparser.ConfigParser()
cfg.read(os.path.expanduser('~/.config/a2pod/config'))
print(cfg.get('podcast', 'name', fallback=''))
" 2>/dev/null)
  echo "✅ AWS already configured (profile: $EXISTING_PROFILE, bucket: $EXISTING_BUCKET)"
  echo "   Feed URL: https://$EXISTING_BUCKET.s3.$EXISTING_REGION.amazonaws.com/feed.xml"

  # Ensure podcast name is set
  if [[ -z "$EXISTING_NAME" ]]; then
    echo ""
    read -p "   Podcast name [A2Pod]: " PODCAST_NAME
    PODCAST_NAME="${PODCAST_NAME:-A2Pod}"
    python3 -c "
import configparser, os
path = os.path.expanduser('~/.config/a2pod/config')
cfg = configparser.ConfigParser()
cfg.read(path)
if not cfg.has_section('podcast'):
    cfg.add_section('podcast')
cfg.set('podcast', 'name', '$PODCAST_NAME')
with open(path, 'w') as f:
    cfg.write(f)
"
    echo "   ✅ Podcast name set to: $PODCAST_NAME"

    # Generate and upload artwork
    echo "   🎨 Generating podcast artwork..."
    python3 "$SCRIPT_DIR/lib/artwork.py" "$PODCAST_NAME" "$CONFIG_DIR/artwork.jpg"
    aws s3 cp "$CONFIG_DIR/artwork.jpg" "s3://$EXISTING_BUCKET/artwork.jpg" \
      --profile "$EXISTING_PROFILE" --content-type "image/jpeg" --quiet 2>/dev/null || true
  else
    echo "   Podcast name: $EXISTING_NAME"
  fi
else
  read -p "   Set up AWS for podcast sync? (y/n): " setup_aws
  if [[ "$setup_aws" =~ ^[Yy]$ ]]; then
    echo ""

    # Detect existing AWS profiles
    PROFILES=""
    if [[ -f "$HOME/.aws/credentials" ]]; then
      PROFILES=$(python3 -c "
import configparser, os
cfg = configparser.ConfigParser()
cfg.read(os.path.expanduser('~/.aws/credentials'))
for s in cfg.sections():
    print(s)
" 2>/dev/null)
    fi

    AWS_PROFILE=""
    if [[ -n "$PROFILES" ]]; then
      echo "   Existing AWS profiles found:"
      i=1
      while IFS= read -r p; do
        echo "   $i) $p"
        i=$((i + 1))
      done <<< "$PROFILES"
      echo "   $i) Enter new credentials"
      echo ""
      read -p "   Choose (1-$i): " choice

      PROFILE_COUNT=$(echo "$PROFILES" | wc -l | tr -d ' ')
      if [[ "$choice" -ge 1 && "$choice" -le "$PROFILE_COUNT" ]]; then
        AWS_PROFILE=$(echo "$PROFILES" | sed -n "${choice}p")
        echo "   Using profile: $AWS_PROFILE"
      fi
    fi

    # If no profile selected, ask for new credentials
    if [[ -z "$AWS_PROFILE" ]]; then
      echo ""
      read -p "   Profile name: " AWS_PROFILE
      read -p "   AWS Access Key ID: " aws_key
      read -s -p "   AWS Secret Access Key: " aws_secret
      echo ""

      mkdir -p ~/.aws
      python3 -c "
import configparser, os
creds_path = os.path.expanduser('~/.aws/credentials')
creds = configparser.ConfigParser()
creds.read(creds_path)
creds['$AWS_PROFILE'] = {
    'aws_access_key_id': '$aws_key',
    'aws_secret_access_key': '$aws_secret'
}
with open(creds_path, 'w') as f:
    creds.write(f)
"
      echo "   ✅ Credentials saved to ~/.aws/credentials"
    fi

    # Get region from profile config or ask
    AWS_REGION=$(python3 -c "
import configparser, os
cfg = configparser.ConfigParser()
cfg.read(os.path.expanduser('~/.aws/config'))
section = 'profile $AWS_PROFILE' if '$AWS_PROFILE' != 'default' else 'default'
print(cfg.get(section, 'region', fallback=''))
" 2>/dev/null)

    if [[ -z "$AWS_REGION" ]]; then
      read -p "   AWS Region [eu-central-1]: " AWS_REGION
      AWS_REGION="${AWS_REGION:-eu-central-1}"

      python3 -c "
import configparser, os
cfg_path = os.path.expanduser('~/.aws/config')
cfg = configparser.ConfigParser()
cfg.read(cfg_path)
section = 'profile $AWS_PROFILE' if '$AWS_PROFILE' != 'default' else 'default'
if not cfg.has_section(section):
    cfg.add_section(section)
cfg.set(section, 'region', '$AWS_REGION')
with open(cfg_path, 'w') as f:
    cfg.write(f)
"
    else
      echo "   Region: $AWS_REGION"
    fi

    # S3 bucket setup
    echo ""
    read -p "   S3 bucket name [my-podcast-feed]: " AWS_BUCKET
    AWS_BUCKET="${AWS_BUCKET:-my-podcast-feed}"

    # Check if bucket exists
    if aws s3api head-bucket --bucket "$AWS_BUCKET" --profile "$AWS_PROFILE" 2>/dev/null; then
      echo "   ✅ Bucket '$AWS_BUCKET' exists"
    else
      echo "   Creating bucket '$AWS_BUCKET'..."
      if [[ "$AWS_REGION" == "us-east-1" ]]; then
        aws s3api create-bucket --bucket "$AWS_BUCKET" --profile "$AWS_PROFILE" --region "$AWS_REGION"
      else
        aws s3api create-bucket --bucket "$AWS_BUCKET" --profile "$AWS_PROFILE" --region "$AWS_REGION" \
          --create-bucket-configuration LocationConstraint="$AWS_REGION"
      fi
      echo "   ✅ Bucket created"
    fi

    # Enable public read access
    echo "   Configuring public access for podcast feed..."
    aws s3api put-public-access-block --bucket "$AWS_BUCKET" --profile "$AWS_PROFILE" \
      --public-access-block-configuration \
      "BlockPublicAcls=false,IgnorePublicAcls=false,BlockPublicPolicy=false,RestrictPublicBuckets=false" \
      2>/dev/null || true

    aws s3api put-bucket-policy --bucket "$AWS_BUCKET" --profile "$AWS_PROFILE" \
      --policy "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Sid\":\"PublicRead\",\"Effect\":\"Allow\",\"Principal\":\"*\",\"Action\":\"s3:GetObject\",\"Resource\":\"arn:aws:s3:::${AWS_BUCKET}/*\"}]}" \
      2>/dev/null || true

    # Podcast name
    echo ""
    read -p "   Podcast name [A2Pod]: " PODCAST_NAME
    PODCAST_NAME="${PODCAST_NAME:-A2Pod}"

    # Save config
    mkdir -p "$CONFIG_DIR"
    cat > "$CONFIG_DIR/config" <<CONF
[aws]
profile = $AWS_PROFILE
bucket = $AWS_BUCKET
region = $AWS_REGION

[podcast]
name = $PODCAST_NAME
CONF

    # Generate and upload podcast artwork
    echo "   🎨 Generating podcast artwork..."
    python3 "$SCRIPT_DIR/lib/artwork.py" "$PODCAST_NAME" "$CONFIG_DIR/artwork.jpg"
    aws s3 cp "$CONFIG_DIR/artwork.jpg" "s3://$AWS_BUCKET/artwork.jpg" \
      --profile "$AWS_PROFILE" --content-type "image/jpeg" --quiet 2>/dev/null || true

    FEED_URL="https://$AWS_BUCKET.s3.$AWS_REGION.amazonaws.com/feed.xml"
    echo ""
    echo "   ✅ Podcast sync configured!"
    echo "   Feed URL: $FEED_URL"
    echo "   Subscribe to this URL in Apple Podcasts on your iPhone."
  else
    echo "   Skipped. Audiobooks will be saved locally only."
    echo "   Run install.sh again to set up later."
  fi
fi

# ─── Optional: Telegram Bot ──────────────────────────────────────────────────

echo ""
echo "🤖 Optional: Enable Telegram bot interface?"
echo "   Lets you send article URLs to a Telegram bot and get audio back."
echo "   The bot runs as a background service whenever your Mac is on."
echo ""

EXISTING_TG_TOKEN=""
if [[ -f "$CONFIG_DIR/config" ]]; then
  EXISTING_TG_TOKEN=$(python3 -c "
import configparser, os
cfg = configparser.ConfigParser()
cfg.read(os.path.expanduser('~/.config/a2pod/config'))
print(cfg.get('telegram', 'bot_token', fallback=''))
" 2>/dev/null)
fi

PLIST_PATH="$HOME/Library/LaunchAgents/com.a2pod.bot.plist"
PYTHON_BIN="$(python3 -c 'import sys; print(sys.executable)')"
PYTHON_DIR="$(dirname "$PYTHON_BIN")"
BOT_SCRIPT="$SCRIPT_DIR/bin/a2pod-bot"
BOT_LOG="$CONFIG_DIR/bot.log"

if [[ -n "$EXISTING_TG_TOKEN" ]]; then
  echo "✅ Telegram bot already configured"

  # Check if launchd service is installed
  if [[ -f "$PLIST_PATH" ]]; then
    echo "✅ Bot service installed (com.a2pod.bot)"
  else
    read -p "   Install bot as background service? (y/n): " install_svc
    if [[ "$install_svc" =~ ^[Yy]$ ]]; then
      cat > "$PLIST_PATH" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.a2pod.bot</string>
    <key>ProgramArguments</key>
    <array>
        <string>$PYTHON_BIN</string>
        <string>$BOT_SCRIPT</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$SCRIPT_DIR</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>$PYTHON_DIR:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
        <key>PYTHONPATH</key>
        <string>$SCRIPT_DIR/lib</string>
    </dict>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>$BOT_LOG</string>
    <key>StandardErrorPath</key>
    <string>$BOT_LOG</string>
</dict>
</plist>
PLIST
      launchctl bootout "gui/$(id -u)" "$PLIST_PATH" 2>/dev/null
      launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH"
      echo "   ✅ Bot service started"
      echo "   Logs: $BOT_LOG"
    fi
  fi
else
  read -p "   Set up Telegram bot? (y/n): " setup_tg
  if [[ "$setup_tg" =~ ^[Yy]$ ]]; then
    echo ""
    echo "   1. Message @BotFather on Telegram and create a new bot"
    echo "   2. Copy the bot token"
    echo ""
    read -s -p "   Bot token: " TG_TOKEN
    echo ""
    echo ""
    echo "   3. Send /start to your bot, then forward a message to @userinfobot"
    echo "      to find your numeric user ID"
    echo ""
    read -p "   Your Telegram user ID(s), comma-separated: " TG_USERS

    mkdir -p "$CONFIG_DIR"
    python3 -c "
import configparser, os
path = os.path.expanduser('~/.config/a2pod/config')
cfg = configparser.ConfigParser()
cfg.read(path)
if not cfg.has_section('telegram'):
    cfg.add_section('telegram')
cfg.set('telegram', 'bot_token', '$TG_TOKEN')
cfg.set('telegram', 'allowed_users', '$TG_USERS')
with open(path, 'w') as f:
    cfg.write(f)
"
    echo "   ✅ Telegram bot configured"

    # Install as background service
    echo ""
    read -p "   Run bot automatically in the background? (y/n): " install_svc
    if [[ "$install_svc" =~ ^[Yy]$ ]]; then
      cat > "$PLIST_PATH" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.a2pod.bot</string>
    <key>ProgramArguments</key>
    <array>
        <string>$PYTHON_BIN</string>
        <string>$BOT_SCRIPT</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$SCRIPT_DIR</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>$PYTHON_DIR:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
        <key>PYTHONPATH</key>
        <string>$SCRIPT_DIR/lib</string>
    </dict>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>$BOT_LOG</string>
    <key>StandardErrorPath</key>
    <string>$BOT_LOG</string>
</dict>
</plist>
PLIST
      launchctl bootout "gui/$(id -u)" "$PLIST_PATH" 2>/dev/null
      launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH"
      echo "   ✅ Bot service started"
      echo "   Logs: $BOT_LOG"
    else
      echo "   Run manually: a2pod-bot"
    fi
  else
    echo "   Skipped. Add later to $CONFIG_DIR/config:"
    echo "   [telegram]"
    echo "   bot_token = YOUR_BOT_TOKEN"
    echo "   allowed_users = YOUR_USER_ID"
  fi
fi

# ─── Done ────────────────────────────────────────────────────────────────────

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "✅ Setup complete!"
echo ""
echo "📌 Usage:"
echo "  a2pod https://some-article.com"
echo "  a2pod https://some-article.com --no-upload"
echo ""
