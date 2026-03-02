"""Single-provider podcast feed management (local filesystem or remote backend)."""

import configparser
import functools
import json
import logging
import os
import socket
import subprocess
from email.utils import formatdate
from pathlib import Path
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

logger = logging.getLogger(__name__)

from backends import get_active_backend

CONFIG_PATH = Path.home() / ".config" / "a2pod" / "config"
OUTPUT_DIR = Path.home() / "A2Pod"
LOCAL_FEED_PATH = OUTPUT_DIR / "feed.xml"

DEFAULT_FEED_TITLE = "A2Pod"
FEED_DESCRIPTION = "Audiobooks converted from articles"
ITUNES_NS = "http://www.itunes.com/dtds/podcast-1.0.dtd"
PODCAST_NS = "https://podcastindex.org/namespace/1.0"
ARTWORK_FILENAME = "artwork.jpg"
AUDIO_CONTENT_TYPE = "audio/x-m4a"

DEFAULT_PORT = 8008

# Register namespaces once at module level
ET.register_namespace("itunes", ITUNES_NS)
ET.register_namespace("podcast", PODCAST_NS)


@functools.lru_cache(maxsize=1)
def _load_config() -> dict:
    """Load config. Always returns at least podcast + server settings.

    Cached for the lifetime of the process (config doesn't change at runtime).
    """
    cfg = configparser.ConfigParser()
    if CONFIG_PATH.exists():
        cfg.read(CONFIG_PATH)
    podcast = cfg["podcast"] if "podcast" in cfg else {}
    server = cfg["server"] if "server" in cfg else {}
    return {
        "podcast_name": podcast.get("name", DEFAULT_FEED_TITLE),
        "port": server.get("port", str(DEFAULT_PORT)),
        "hostname": server.get("hostname", ""),
    }


def _get_local_base_url() -> str:
    config = _load_config()
    host = config["hostname"]
    if not host:
        host = _get_lan_ip()
    port = int(config["port"])
    return f"http://{host}:{port}"


def _get_lan_ip() -> str:
    """Get this machine's LAN IP address."""
    try:
        # Connect to a public IP (doesn't send data) to find the local interface
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


# ─── Provider-aware I/O ──────────────────────────────────────────────────────

def _get_base_url() -> str:
    """Return the base URL for the active provider."""
    backend = get_active_backend()
    if backend is None:
        return _get_local_base_url()
    return backend.get_base_url()


def _read_feed() -> ET.Element | None:
    """Read the feed from the active provider. Returns parsed XML or None."""
    backend = get_active_backend()
    if backend is None:
        if not LOCAL_FEED_PATH.exists():
            return None
        raw = LOCAL_FEED_PATH.read_text(encoding="utf-8")
        return _parse_feed_xml(raw)
    raw = backend.read_feed()
    if raw is None:
        return None
    return _parse_feed_xml(raw)


def _write_feed(rss: ET.Element) -> None:
    """Write the feed to the active provider."""
    backend = get_active_backend()
    xml_content = _serialize_feed(rss)
    if backend is None:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        LOCAL_FEED_PATH.write_text(xml_content, encoding="utf-8")
    else:
        backend.write_feed(xml_content)


# ─── Feed URL accessors ──────────────────────────────────────────────────────

def get_feed_url() -> str:
    """Return the feed URL for the active provider."""
    backend = get_active_backend()
    if backend is None:
        return f"{_get_local_base_url()}/feed.xml"
    return backend.get_feed_url()


def ensure_feed_exists() -> None:
    """Create feed.xml if it doesn't exist yet (local provider only)."""
    backend = get_active_backend()
    if backend is not None:
        return
    if LOCAL_FEED_PATH.exists():
        return
    config = _load_config()
    base_url = _get_local_base_url()
    rss = _build_fresh_feed(base_url, config)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    LOCAL_FEED_PATH.write_text(_serialize_feed(rss), encoding="utf-8")


# ─── Feed XML helpers ─────────────────────────────────────────────────────────

def _parse_feed_xml(raw: str) -> ET.Element:
    """Parse feed XML string, deduplicating xmlns:itunes if present (legacy bug)."""
    ns_decl = f' xmlns:itunes="{ITUNES_NS}"'
    while raw.count(ns_decl) > 1:
        idx = raw.rindex(ns_decl)
        raw = raw[:idx] + raw[idx + len(ns_decl):]
    return ET.fromstring(raw)


def _serialize_feed(rss: ET.Element) -> str:
    """Serialize RSS element to XML string."""
    for attr in list(rss.attrib):
        if attr.startswith("xmlns"):
            del rss.attrib[attr]
    ET.indent(rss, space="  ")
    return '<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(rss, encoding="unicode")


# ─── Feed construction ────────────────────────────────────────────────────────

def _build_fresh_feed(base_url: str, config: dict) -> ET.Element:
    """Create a minimal valid podcast RSS feed."""
    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    podcast_name = config.get("podcast_name", DEFAULT_FEED_TITLE)
    ET.SubElement(channel, "title").text = podcast_name
    ET.SubElement(channel, "description").text = FEED_DESCRIPTION
    ET.SubElement(channel, "link").text = f"{base_url}/feed.xml"
    ET.SubElement(channel, "language").text = "en"
    ET.SubElement(channel, "{%s}author" % ITUNES_NS).text = podcast_name
    ET.SubElement(channel, "{%s}image" % ITUNES_NS, {"href": f"{base_url}/{ARTWORK_FILENAME}"})
    ET.SubElement(channel, "{%s}category" % ITUNES_NS, {"text": "Technology"})
    return rss


def _update_channel_metadata(rss: ET.Element, base_url: str, config: dict) -> None:
    """Update channel-level metadata to match current config."""
    channel = rss.find("channel")
    podcast_name = config.get("podcast_name", DEFAULT_FEED_TITLE)

    title_el = channel.find("title")
    if title_el is not None:
        title_el.text = podcast_name
    author_el = channel.find("{%s}author" % ITUNES_NS)
    if author_el is not None:
        author_el.text = podcast_name

    link_el = channel.find("link")
    if link_el is not None:
        link_el.text = f"{base_url}/feed.xml"

    image_el = channel.find("{%s}image" % ITUNES_NS)
    if image_el is None:
        ET.SubElement(channel, "{%s}image" % ITUNES_NS, {"href": f"{base_url}/{ARTWORK_FILENAME}"})
    else:
        image_el.set("href", f"{base_url}/{ARTWORK_FILENAME}")


def _add_feed_item(rss: ET.Element, title: str, enclosure_url: str,
                   file_size: int, duration_seconds: int | None = None,
                   source_url: str | None = None, summary: str | None = None,
                   transcript_url: str | None = None,
                   voice_name: str | None = None) -> None:
    """Prepend a new <item> to the RSS channel (newest first)."""
    channel = rss.find("channel")

    item = ET.Element("item")
    ET.SubElement(item, "title").text = title
    ET.SubElement(item, "guid", {"isPermaLink": "false"}).text = enclosure_url
    ET.SubElement(item, "pubDate").text = formatdate(usegmt=True)
    if voice_name:
        ET.SubElement(item, "{%s}author" % ITUNES_NS).text = voice_name
    if source_url:
        domain = urlparse(source_url).netloc.removeprefix("www.")
        ET.SubElement(item, "{%s}subtitle" % ITUNES_NS).text = domain
        ET.SubElement(item, "link").text = source_url
    if summary:
        episode_desc = f"Narrated by {voice_name}. {summary}" if voice_name else summary
        ET.SubElement(item, "description").text = episode_desc
        ET.SubElement(item, "{%s}summary" % ITUNES_NS).text = episode_desc
    ET.SubElement(item, "enclosure", {
        "url": enclosure_url,
        "length": str(file_size),
        "type": AUDIO_CONTENT_TYPE,
    })
    if duration_seconds:
        h = duration_seconds // 3600
        m = (duration_seconds % 3600) // 60
        s = duration_seconds % 60
        ET.SubElement(item, "{%s}duration" % ITUNES_NS).text = f"{h:02d}:{m:02d}:{s:02d}"
    if transcript_url:
        ET.SubElement(item, "{%s}transcript" % PODCAST_NS, {
            "url": transcript_url,
            "type": "text/vtt",
            "language": "en",
            "rel": "captions",
        })

    items = channel.findall("item")
    if items:
        idx = list(channel).index(items[0])
        channel.insert(idx, item)
    else:
        channel.append(item)


def _get_duration_seconds(filepath: str) -> int | None:
    """Use ffprobe to get audio duration in seconds."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", filepath],
            capture_output=True, text=True, check=True,
        )
        data = json.loads(result.stdout)
        return int(float(data["format"]["duration"]))
    except Exception:
        return None


# ─── Shared helpers ───────────────────────────────────────────────────────────

def _find_matching_item(channel: ET.Element, query: str) -> ET.Element | None:
    """Find a feed <item> matching query by URL (exact) or title (substring)."""
    query_lower = query.lower().strip()
    normalized_url = query.rstrip("/")
    for item in channel.findall("item"):
        link = item.findtext("link", "")
        title = item.findtext("title", "")
        if (link and link.rstrip("/") == normalized_url) or \
           (title and query_lower in title.lower()):
            return item
    return None


def _collect_item_paths(item: ET.Element, base_url: str) -> list[str]:
    """Extract URL path suffixes (filenames or remote keys) from a feed <item>."""
    paths = []
    prefix = base_url + "/"
    enclosure = item.find("enclosure")
    if enclosure is not None:
        url = enclosure.get("url", "")
        if url.startswith(prefix):
            paths.append(url[len(prefix):])
    transcript = item.find("{%s}transcript" % PODCAST_NS)
    if transcript is not None:
        url = transcript.get("url", "")
        if url.startswith(prefix):
            paths.append(url[len(prefix):])
    return paths


def _cleanup_local_files(paths: list[str]) -> int:
    """Delete local .m4a/.vtt files after successful remote upload. Returns count deleted."""
    deleted = 0
    for path in paths:
        p = Path(path)
        if p.exists():
            p.unlink()
            deleted += 1
            logger.info("Cleaned up local file: %s", p)
    return deleted


# ─── Episode operations ───────────────────────────────────────────────────────

def find_existing_episode(url: str) -> dict | None:
    """Check if a URL was already processed by searching the feed.

    Returns dict with title, audio_url, summary, feed_url, cached=True, or None.
    """
    rss = _read_feed()
    if rss is None:
        return None

    normalized = url.rstrip("/")
    channel = rss.find("channel")
    for item in channel.findall("item"):
        link = item.findtext("link", "")
        if link.rstrip("/") == normalized:
            enclosure = item.find("enclosure")
            audio_url = enclosure.get("url") if enclosure is not None else None
            return {
                "title": item.findtext("title", ""),
                "audio_url": audio_url,
                "summary": item.findtext("description", ""),
                "feed_url": get_feed_url(),
                "cached": True,
            }
    return None


def find_episode(query: str) -> dict | None:
    """Search feed by URL (exact match) or title (case-insensitive substring).

    Returns {"title", "link", "audio_url"} or None.
    """
    rss = _read_feed()
    if rss is None:
        return None

    item = _find_matching_item(rss.find("channel"), query)
    if item is None:
        return None
    enclosure = item.find("enclosure")
    return {
        "title": item.findtext("title", ""),
        "link": item.findtext("link", ""),
        "audio_url": enclosure.get("url") if enclosure is not None else None,
    }


def list_episodes() -> list[dict]:
    """Return list of {"title", "link"} for all feed items."""
    rss = _read_feed()
    if rss is None:
        return []

    channel = rss.find("channel")
    return [
        {"title": item.findtext("title", ""), "link": item.findtext("link", "")}
        for item in channel.findall("item")
    ]


def publish_episode(local_path: str, title: str, source_url: str | None = None,
                    summary: str | None = None, transcript_path: str | None = None,
                    voice_name: str | None = None) -> str:
    """Publish an episode to the active provider's feed.

    When provider=s3: uploads files, updates remote feed, deletes local .m4a/.vtt.
    When provider=local: updates local feed, files stay.

    Returns the feed URL.
    """
    config = _load_config()
    backend = get_active_backend()
    base_url = _get_base_url()
    file_size = os.path.getsize(local_path)
    filename = os.path.basename(local_path)
    duration = _get_duration_seconds(local_path)

    if backend is not None:
        # Remote provider: upload files, build remote URLs
        remote_key = backend.remote_key(filename)
        backend.upload_file(local_path, remote_key, AUDIO_CONTENT_TYPE)
        enclosure_url = f"{base_url}/{remote_key}"

        transcript_url = None
        if transcript_path and os.path.exists(transcript_path):
            vtt_filename = os.path.basename(transcript_path)
            vtt_key = backend.remote_key(vtt_filename)
            backend.upload_file(transcript_path, vtt_key, "text/vtt; charset=utf-8")
            transcript_url = f"{base_url}/{vtt_key}"

        # Read or create remote feed
        rss = _read_feed() or _build_fresh_feed(base_url, config)
        _update_channel_metadata(rss, base_url, config)
        _add_feed_item(rss, title, enclosure_url, file_size, duration, source_url, summary,
                       transcript_url, voice_name)
        _write_feed(rss)

        # Clean up local files after successful upload
        local_files = [local_path]
        if transcript_path and os.path.exists(transcript_path):
            local_files.append(transcript_path)
        _cleanup_local_files(local_files)
    else:
        # Local provider: flat filenames, local feed
        enclosure_url = f"{base_url}/{filename}"
        transcript_url = None
        if transcript_path and os.path.exists(transcript_path):
            vtt_filename = os.path.basename(transcript_path)
            transcript_url = f"{base_url}/{vtt_filename}"

        rss = _read_feed() or _build_fresh_feed(base_url, config)
        _update_channel_metadata(rss, base_url, config)
        _add_feed_item(rss, title, enclosure_url, file_size, duration, source_url, summary,
                       transcript_url, voice_name)
        _write_feed(rss)

    return get_feed_url()


def delete_episode(query: str) -> dict:
    """Delete a single episode matching query (URL or title substring).

    Removes from feed and deletes files from the active provider.
    Returns {"title", "files_deleted"}.
    """
    from errors import PipelineError

    rss = _read_feed()
    if rss is None:
        raise PipelineError("No podcast feed found.")

    channel = rss.find("channel")
    matched_item = _find_matching_item(channel, query)
    if matched_item is None:
        raise PipelineError(f"No episode found matching: {query}")

    title = matched_item.findtext("title", "")
    backend = get_active_backend()
    base_url = _get_base_url()
    paths = _collect_item_paths(matched_item, base_url)

    channel.remove(matched_item)
    _write_feed(rss)

    files_deleted = 0
    if backend is not None:
        for key in paths:
            try:
                backend.delete_file(key)
                files_deleted += 1
            except Exception:
                pass
    else:
        for filename in paths:
            filepath = OUTPUT_DIR / filename
            if filepath.exists():
                filepath.unlink()
                files_deleted += 1

    return {"title": title, "files_deleted": files_deleted}


def delete_all_episodes() -> dict:
    """Remove all episodes from the feed and delete all files.

    Operates on the active provider only. Returns {"episodes_deleted", "files_deleted"}.
    """
    backend = get_active_backend()
    base_url = _get_base_url()
    config = _load_config()

    rss = _read_feed()
    if rss is None:
        rss = _build_fresh_feed(base_url, config)

    channel = rss.find("channel")
    items = channel.findall("item")
    episodes_deleted = len(items)

    # Collect file paths before removing items
    all_paths = []
    for item in items:
        all_paths.extend(_collect_item_paths(item, base_url))

    for item in items:
        channel.remove(item)
    _write_feed(rss)

    files_deleted = 0
    if backend is not None:
        files_deleted = backend.delete_files_by_prefix()
    else:
        for filename in all_paths:
            filepath = OUTPUT_DIR / filename
            if filepath.exists():
                filepath.unlink()
                files_deleted += 1

    return {"episodes_deleted": episodes_deleted, "files_deleted": files_deleted}
