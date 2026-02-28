"""Text extraction from URLs and files."""

import configparser
import json
import re
import sys
import urllib.error
import urllib.request
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

_CONFIG_PATH = Path.home() / ".config" / "a2pod" / "config"
_X_API_BASE = "https://api.x.com/2/tweets"


def is_x_url(url: str) -> bool:
    """Check if URL is an X/Twitter link."""
    return bool(re.match(r"https?://(www\.)?(twitter\.com|x\.com)/", url))


def _is_x_article_url(url: str) -> bool:
    """Check if URL is an X Article (long-form content)."""
    return bool(re.search(r"(?:twitter\.com|x\.com)/\w+/article/\d+", url))


def _get_x_bearer_token() -> str:
    """Read X API bearer token from config file."""
    cfg = configparser.RawConfigParser()
    cfg.read(_CONFIG_PATH)
    token = cfg.get("x", "bearer_token", fallback="").strip()
    if not token:
        print("❌ X API bearer token not configured.")
        print(f"   Add it to {_CONFIG_PATH}:")
        print()
        print("   [x]")
        print("   bearer_token = YOUR_TOKEN_HERE")
        print()
        sys.exit(1)
    return token


def _extract_post_id(url: str) -> str:
    """Extract post ID from an X/Twitter URL (/status/ or /article/)."""
    match = re.search(r"(?:twitter\.com|x\.com)/\w+/(?:status|article)/(\d+)", url)
    if not match:
        print(f"❌ Could not extract post ID from URL: {url}")
        sys.exit(1)
    return match.group(1)


def _x_api_fetch(post_id: str, token: str) -> dict:
    """Call X API v2 tweet lookup and return parsed JSON."""
    params = (
        "tweet.fields=note_tweet,article,author_id,created_at,text"
        "&expansions=author_id"
        "&user.fields=name,username"
    )
    api_url = f"{_X_API_BASE}/{post_id}?{params}"

    req = urllib.request.Request(api_url, headers={
        "Authorization": f"Bearer {token}",
    })

    try:
        resp = urllib.request.urlopen(req, timeout=15)
        return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode() if e.fp else ""
        if e.code == 401:
            print("❌ X API authentication failed (401).")
            print("   Check your bearer token in the config file.")
        elif e.code == 403:
            print("❌ X API access forbidden (403).")
            print("   Your API plan may not include this endpoint.")
        elif e.code == 404:
            print("❌ Post not found (404). It may have been deleted.")
        elif e.code == 429:
            print("❌ X API rate limit exceeded (429). Try again later.")
        else:
            print(f"❌ X API returned HTTP {e.code}: {body[:200]}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Could not reach X API: {e}")
        sys.exit(1)


def _extract_text_from_tweet(tweet: dict) -> tuple[str, bool]:
    """Extract the best available text from a tweet object.

    Returns (text, is_article). Tries article body, note_tweet, then text.
    """
    # Try article field — check every plausible key for body content
    article = tweet.get("article")
    if article and isinstance(article, dict):
        for key in ("text", "body", "content", "html_content"):
            val = article.get(key)
            if val and isinstance(val, str) and len(val) > 100:
                return val, True
        # Last resort: find the longest string value in the article object
        longest = ""
        for val in article.values():
            if isinstance(val, str) and len(val) > len(longest):
                longest = val
        if len(longest) > 100:
            return longest, True

    # Try note_tweet (long posts >280 chars)
    note = tweet.get("note_tweet")
    if note and isinstance(note, dict):
        note_text = note.get("text", "")
        if note_text:
            return note_text, False

    # Fall back to standard text field
    return tweet.get("text", ""), False


def _get_x_author_info(data: dict, url: str) -> tuple[str | None, str]:
    """Extract display name and username from API response or URL."""
    username = None
    display_name = None
    users = data.get("includes", {}).get("users", [])
    if users:
        display_name = users[0].get("name")
        username = users[0].get("username")
    if not username:
        match = re.search(r"(?:twitter\.com|x\.com)/(\w+)/(?:status|article)", url)
        username = match.group(1) if match else "unknown"
    return display_name, username


def extract_from_x(url: str) -> tuple[str, str]:
    """Extract post or article text using the X API v2."""
    token = _get_x_bearer_token()
    post_id = _extract_post_id(url)
    is_article_url = _is_x_article_url(url)

    data = _x_api_fetch(post_id, token)

    if "errors" in data and "data" not in data:
        err = data["errors"][0]
        print(f"❌ X API error: {err.get('detail', err.get('title', 'Unknown error'))}")
        sys.exit(1)

    tweet = data["data"]
    text, is_article = _extract_text_from_tweet(tweet)

    # For article URLs: if API didn't return article body, dump response for debugging
    if is_article_url and not is_article and len(text.split()) < 100:
        print()
        print("❌ X API did not return the article body text.")
        print("   The API returned these fields for this tweet:")
        for key, val in tweet.items():
            if key == "text":
                print(f"   - text: {val[:80]}...")
            elif isinstance(val, dict):
                print(f"   - {key}: {json.dumps(val, indent=2)[:200]}")
            else:
                print(f"   - {key}: {val}")
        print()
        print("   The 'article' field may require a Pro or Enterprise X API plan.")
        sys.exit(1)

    if not text.strip():
        print("❌ Post has no text content.")
        sys.exit(1)

    display_name, username = _get_x_author_info(data, url)
    content_type = "article" if (is_article or is_article_url) else "post"
    if display_name:
        title = f"{display_name} (@{username}) — {content_type}"
    else:
        title = f"@{username} — {content_type}"

    return text, title


def _fetch_html(url: str) -> str:
    """Fetch HTML content from a URL, with browser User-Agent fallback."""
    import trafilatura

    downloaded = trafilatura.fetch_url(url)
    if not downloaded:
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
    return downloaded


def _extract_from_html(downloaded: str) -> tuple[str, str | None]:
    """Extract text and title from downloaded HTML."""
    import trafilatura

    text = trafilatura.extract(
        downloaded, include_comments=False, include_tables=False
    )
    if not text or len(text.strip()) < 100:
        print("❌ Could not extract meaningful text from this URL.")
        print("   The page might be JS-heavy or paywalled.")
        sys.exit(1)

    word_count = len(text.split())
    if word_count < MIN_ARTICLE_WORDS or _is_error_page(text):
        print("❌ This URL appears to be an error page (404 / removed content).")
        print("   Check the URL and try again.")
        sys.exit(1)

    metadata = trafilatura.extract_metadata(downloaded)
    title = metadata.title if metadata and metadata.title else None
    return text, title


def extract_from_url(url: str) -> tuple[str, str]:
    """Extract article text and title from a URL."""
    if is_x_url(url):
        label = "X article" if _is_x_article_url(url) else "X post"
        print(f"  Detected {label} — fetching via API...", end="", flush=True)
        text, title = extract_from_x(url)
        print(" done")
        return text, title

    downloaded = _fetch_html(url)
    text, title = _extract_from_html(downloaded)
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
