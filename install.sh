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
pip3 install mlx-audio trafilatura soundfile "misaki[en]" phonemizer espeakng_loader boto3 mutagen Pillow --quiet

# Pre-download Kokoro model
echo "🧠 Downloading Kokoro TTS model (~160MB)..."
python3 -c "
from mlx_audio.tts.utils import load_model
model = load_model('mlx-community/Kokoro-82M-bf16')
print('✅ Model cached')
"

# ─── Make scripts executable ─────────────────────────────────────────────────

chmod +x "$SCRIPT_DIR/bin/a2pod"

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

# ─── Optional: Ollama for episode summaries ──────────────────────────────────

echo ""
echo "📝 Optional: Enable AI episode summaries via Ollama?"
echo "   Generates 2-3 sentence descriptions for each episode in the podcast feed."
echo ""

if command -v ollama &>/dev/null; then
  echo "✅ Ollama is installed"
  # Check if default model is available
  if ollama list 2>/dev/null | grep -q "llama3.2"; then
    echo "✅ llama3.2 model is available"
  else
    read -p "   Pull llama3.2 model (~2GB)? (y/n): " pull_model
    if [[ "$pull_model" =~ ^[Yy]$ ]]; then
      # Ensure server is running
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
