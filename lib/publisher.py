"""Local-first podcast feed management with optional remote backend sync."""

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

from backends import get_configured_backends

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


@functools.lru_cache(maxsize=1)
def _get_local_base_url() -> str:
    config = _load_config()
    hostname = config["hostname"]
    if not hostname:
        hostname = socket.gethostname()
        if not hostname.endswith(".local"):
            hostname += ".local"
    port = int(config["port"])
    return f"http://{hostname}:{port}"


# ─── Feed URL accessors ──────────────────────────────────────────────────────

def get_feed_url() -> str:
    """Return the local feed URL (always available)."""
    return f"{_get_local_base_url()}/feed.xml"


def get_remote_feed_urls() -> list[str]:
    """Return feed URLs from all configured remote backends."""
    return [b.get_feed_url() for b in get_configured_backends()]


# ─── Local feed I/O ──────────────────────────────────────────────────────────

def _parse_feed_xml(raw: str) -> ET.Element:
    """Parse feed XML string, deduplicating xmlns:itunes if present (legacy bug)."""
    ns_decl = f' xmlns:itunes="{ITUNES_NS}"'
    while raw.count(ns_decl) > 1:
        idx = raw.rindex(ns_decl)
        raw = raw[:idx] + raw[idx + len(ns_decl):]
    return ET.fromstring(raw)


def _read_local_feed() -> ET.Element | None:
    """Read feed.xml from ~/A2Pod/. Returns None if not found."""
    if not LOCAL_FEED_PATH.exists():
        return None
    raw = LOCAL_FEED_PATH.read_text(encoding="utf-8")
    return _parse_feed_xml(raw)


def _write_local_feed(rss: ET.Element) -> None:
    """Write feed.xml to ~/A2Pod/."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    xml_content = _serialize_feed(rss)
    LOCAL_FEED_PATH.write_text(xml_content, encoding="utf-8")


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


# ─── Episode operations ───────────────────────────────────────────────────────

def find_existing_episode(url: str) -> dict | None:
    """Check if a URL was already processed by searching the local feed.

    Returns dict with title, audio_url, summary, feed_url, cached=True, or None.
    """
    rss = _read_local_feed()
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
    """Search local feed by URL (exact match) or title (case-insensitive substring).

    Returns {"title", "link", "audio_url"} or None.
    """
    rss = _read_local_feed()
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
    rss = _read_local_feed()
    if rss is None:
        return []

    channel = rss.find("channel")
    return [
        {"title": item.findtext("title", ""), "link": item.findtext("link", "")}
        for item in channel.findall("item")
    ]


def publish_episode(local_path: str, title: str, source_url: str | None = None,
                    summary: str | None = None, transcript_path: str | None = None,
                    voice_name: str | None = None, no_upload: bool = False) -> str:
    """Publish an episode: always updates local feed, optionally syncs to remote backends.

    Args:
        no_upload: If True, skip remote backend sync (local feed still updated).

    Returns the local feed URL.
    """
    config = _load_config()
    base_url = _get_local_base_url()
    file_size = os.path.getsize(local_path)
    filename = os.path.basename(local_path)
    duration = _get_duration_seconds(local_path)

    # Local feed URLs are flat (no subdirectory prefix)
    enclosure_url = f"{base_url}/{filename}"
    transcript_url = None
    if transcript_path and os.path.exists(transcript_path):
        vtt_filename = os.path.basename(transcript_path)
        transcript_url = f"{base_url}/{vtt_filename}"

    # Read or create local feed
    rss = _read_local_feed() or _build_fresh_feed(base_url, config)
    _update_channel_metadata(rss, base_url, config)

    _add_feed_item(rss, title, enclosure_url, file_size, duration, source_url, summary,
                   transcript_url, voice_name)
    _write_local_feed(rss)

    # Sync to remote backends
    if not no_upload:
        for backend in get_configured_backends():
            try:
                backend.sync_episode(local_path, transcript_path, title, file_size,
                                     duration, source_url, summary, voice_name, config)
            except Exception as e:
                logger.warning("Remote sync failed for %s: %s", backend.__class__.__name__, e)

    return get_feed_url()


def delete_episode(query: str) -> dict:
    """Delete a single episode matching query (URL or title substring).

    Removes from local feed, deletes local files, then syncs to remote backends.
    Returns {"title", "files_deleted"}.
    """
    from errors import PipelineError

    rss = _read_local_feed()
    if rss is None:
        raise PipelineError("No podcast feed found.")

    channel = rss.find("channel")
    matched_item = _find_matching_item(channel, query)
    if matched_item is None:
        raise PipelineError(f"No episode found matching: {query}")

    title = matched_item.findtext("title", "")
    base_url = _get_local_base_url()
    local_files = _collect_item_paths(matched_item, base_url)

    channel.remove(matched_item)
    _write_local_feed(rss)

    # Delete local files
    files_deleted = 0
    for filename in local_files:
        filepath = OUTPUT_DIR / filename
        if filepath.exists():
            filepath.unlink()
            files_deleted += 1

    # Sync deletions to remote backends
    for backend in get_configured_backends():
        try:
            _delete_episode_from_backend(backend, query)
        except Exception as e:
            logger.warning("Remote delete failed for %s: %s", backend.__class__.__name__, e)

    return {"title": title, "files_deleted": files_deleted}


def _delete_episode_from_backend(backend, query: str) -> None:
    """Delete an episode from a remote backend's feed and files."""
    raw = backend.read_feed()
    if not raw:
        return

    rss = _parse_feed_xml(raw)
    channel = rss.find("channel")
    item = _find_matching_item(channel, query)
    if item is None:
        return

    remote_base = backend.get_base_url()
    for key in _collect_item_paths(item, remote_base):
        try:
            backend.delete_file(key)
        except Exception:
            pass
    channel.remove(item)
    backend.write_feed(_serialize_feed(rss))


def delete_all_episodes() -> dict:
    """Remove all episodes from local feed and delete local audio/vtt files.

    Also syncs to remote backends. Returns {"episodes_deleted", "files_deleted"}.
    """
    rss = _read_local_feed()
    if rss is None:
        config = _load_config()
        rss = _build_fresh_feed(_get_local_base_url(), config)

    channel = rss.find("channel")
    items = channel.findall("item")
    episodes_deleted = len(items)

    # Collect local files before removing items
    base_url = _get_local_base_url()
    all_local_files = []
    for item in items:
        all_local_files.extend(_collect_item_paths(item, base_url))

    for item in items:
        channel.remove(item)
    _write_local_feed(rss)

    # Delete local audio/vtt files
    files_deleted = 0
    for filename in all_local_files:
        filepath = OUTPUT_DIR / filename
        if filepath.exists():
            filepath.unlink()
            files_deleted += 1

    # Sync to remote backends
    for backend in get_configured_backends():
        try:
            _delete_all_from_backend(backend)
        except Exception as e:
            logger.warning("Remote delete-all failed for %s: %s", backend.__class__.__name__, e)

    return {"episodes_deleted": episodes_deleted, "files_deleted": files_deleted}


def _delete_all_from_backend(backend) -> None:
    """Clear all episodes from a remote backend."""
    raw = backend.read_feed()
    if raw:
        rss = _parse_feed_xml(raw)
        channel = rss.find("channel")
        for item in channel.findall("item"):
            channel.remove(item)
        backend.write_feed(_serialize_feed(rss))

    backend.delete_files_by_prefix()
