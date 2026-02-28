"""Text extraction from URLs and files."""

import re
import subprocess
import sys
from pathlib import Path

# Patterns that indicate an error page rather than real article content
_ERROR_PAGE_PATTERNS = re.compile(
    r"(?i)\b("
    r"page\s*(not\s*found|doesn'?t\s*exist|could\s*not\s*be\s*found|is\s*no\s*longer\s*available|has\s*been\s*(removed|deleted|moved))"
    r"|404\s*(error|not\s*found|page)"
    r"|not\s*found.*?(requested|looking\s*for)"
    r"|this\s*page\s*(doesn'?t|does\s*not)\s*exist"
    r"|the\s*(article|page|post|content)\s*(you\s*(are|'re)\s*looking\s*for|was\s*(not\s*found|removed|deleted|moved))"
    r"|we\s*couldn'?t\s*find\s*(that|the|this)\s*(page|article|post)"
    r"|nothing\s*(was\s*)?found\s*(here|at\s*this)"
    r"|error\s*404"
    r"|sorry.*?(can'?t|couldn'?t|unable\s*to)\s*find"
    r"|content\s*(is\s*)?(unavailable|no\s*longer\s*available)"
    r"|oops.*?(wrong|lost|missing|find)"
    r"|expired\s*link"
    r"|broken\s*link"
    r")\b"
)

MIN_ARTICLE_WORDS = 50


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
    import urllib.request
    import urllib.error

    downloaded = trafilatura.fetch_url(url)
    if not downloaded:
        # Some sites block trafilatura's User-Agent; retry with a browser-like one
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
            })
            resp = urllib.request.urlopen(req, timeout=15)
            if resp.status >= 400:
                print(f"❌ URL returned HTTP {resp.status}: {url}")
                sys.exit(1)
            downloaded = resp.read().decode()
        except urllib.error.HTTPError as e:
            print(f"❌ URL returned HTTP {e.code}: {url}")
            sys.exit(1)
        except Exception:
            pass
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

    # Detect error pages (404s, removed content, etc.)
    word_count = len(text.split())
    if word_count < MIN_ARTICLE_WORDS or _is_error_page(text):
        print("❌ This URL appears to be an error page (404 / removed content).")
        print("   Check the URL and try again.")
        sys.exit(1)

    metadata = trafilatura.extract_metadata(downloaded)
    title = metadata.title if metadata and metadata.title else None
    return text, title


def _is_error_page(text: str) -> bool:
    """Check if extracted text looks like an error/404 page."""
    # Short text with error patterns is almost certainly an error page
    if _ERROR_PAGE_PATTERNS.search(text) and len(text.split()) < 300:
        return True
    # Very high ratio of error signals in the text
    matches = len(_ERROR_PAGE_PATTERNS.findall(text))
    if matches >= 2:
        return True
    return False


def extract_from_file(filepath: str) -> str:
    """Read text from a local file."""
    p = Path(filepath)
    if not p.exists():
        print(f"❌ File not found: {filepath}")
        sys.exit(1)
    return p.read_text(encoding="utf-8")
