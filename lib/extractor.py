"""Text extraction from URLs and files."""

import re
import subprocess
import sys
from pathlib import Path


def is_x_url(url: str) -> bool:
    """Check if URL is an X/Twitter link."""
    return bool(re.match(r"https?://(www\.)?(twitter\.com|x\.com)/", url))


def extract_from_x(url: str) -> tuple[str, str]:
    """Extract tweet or thread text using bird CLI."""
    for cmd in ["thread", "read"]:
        try:
            result = subprocess.run(
                ["bird", cmd, url],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0 and result.stdout.strip():
                text = result.stdout.strip()
                match = re.search(r"(?:twitter\.com|x\.com)/(\w+)/status", url)
                title = f"@{match.group(1)} thread" if match else "X Post"
                return text, title
        except (subprocess.TimeoutExpired, FileNotFoundError):
            continue

    print("❌ Could not fetch X/Twitter content.")
    print("   Make sure bird is installed and authenticated: bird check")
    sys.exit(1)


def extract_from_url(url: str) -> tuple[str, str]:
    """Extract article text and title from a URL."""
    if is_x_url(url):
        print("🐦 Detected X/Twitter URL — using bird CLI")
        return extract_from_x(url)

    import trafilatura

    downloaded = trafilatura.fetch_url(url)
    if not downloaded:
        print(f"❌ Could not fetch URL: {url}")
        sys.exit(1)

    text = trafilatura.extract(
        downloaded, include_comments=False, include_tables=False
    )
    if not text or len(text.strip()) < 100:
        print("❌ Could not extract meaningful text from this URL.")
        print("   The page might be JS-heavy or paywalled.")
        sys.exit(1)

    metadata = trafilatura.extract_metadata(downloaded)
    title = metadata.title if metadata and metadata.title else None
    return text, title


def extract_from_file(filepath: str) -> str:
    """Read text from a local file."""
    p = Path(filepath)
    if not p.exists():
        print(f"❌ File not found: {filepath}")
        sys.exit(1)
    return p.read_text(encoding="utf-8")
