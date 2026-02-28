#!/bin/bash
# Background queue processor — runs via launchd every 5 minutes.
# Checks iCloud queue file, processes new URLs, notifies on completion.

set -e

QUEUE_DIR="$HOME/Library/Mobile Documents/com~apple~CloudDocs/A2Pod"
QUEUE_FILE="$QUEUE_DIR/queue.txt"
DONE_FILE="$QUEUE_DIR/done.txt"
LOCK_FILE="/tmp/a2pod.lock"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Prevent concurrent runs
if [ -f "$LOCK_FILE" ]; then
  pid=$(cat "$LOCK_FILE" 2>/dev/null)
  if kill -0 "$pid" 2>/dev/null; then
    exit 0
  fi
fi
echo $$ > "$LOCK_FILE"
trap "rm -f $LOCK_FILE" EXIT

# Ensure dirs exist
mkdir -p "$QUEUE_DIR"
touch "$QUEUE_FILE" "$DONE_FILE"

# Exit if queue is empty
if [ ! -s "$QUEUE_FILE" ]; then
  exit 0
fi

# Process each URL
while IFS= read -r url || [ -n "$url" ]; do
  url=$(echo "$url" | xargs)  # trim whitespace
  [ -z "$url" ] && continue

  echo "$(date '+%Y-%m-%d %H:%M') Processing: $url"

  if "$SCRIPT_DIR/bin/a2pod" "$url" 2>&1; then
    echo "$(date '+%Y-%m-%d %H:%M') ✅ $url" >> "$DONE_FILE"
    osascript -e "display notification \"Audiobook ready in Books\" with title \"A2Pod ✅\" sound name \"Glass\""
  else
    echo "$(date '+%Y-%m-%d %H:%M') ❌ FAILED: $url" >> "$DONE_FILE"
    osascript -e "display notification \"Failed: $url\" with title \"A2Pod ❌\" sound name \"Basso\""
  fi
done < "$QUEUE_FILE"

# Clear the queue
> "$QUEUE_FILE"
