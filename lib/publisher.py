"""S3 upload and podcast RSS feed management for a2pod."""

import configparser
import json
import os
import subprocess
from email.utils import formatdate
from pathlib import Path
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

CONFIG_PATH = Path.home() / ".config" / "a2pod" / "config"
FEED_KEY = "feed.xml"
AUDIOBOOKS_PREFIX = "audiobooks/"

DEFAULT_FEED_TITLE = "A2Pod"
FEED_DESCRIPTION = "Audiobooks converted from articles"
ITUNES_NS = "http://www.itunes.com/dtds/podcast-1.0.dtd"
PODCAST_NS = "https://podcastindex.org/namespace/1.0"
ARTWORK_KEY = "artwork.jpg"


def _load_config() -> dict | None:
    """Load config from ~/.config/a2pod/config. Returns None if not configured."""
    if not CONFIG_PATH.exists():
        return None
    cfg = configparser.ConfigParser()
    cfg.read(CONFIG_PATH)
    if "aws" not in cfg:
        return None
    section = cfg["aws"]
    profile = section.get("profile", "")
    bucket = section.get("bucket", "")
    region = section.get("region", "")
    if not profile or not bucket or not region:
        return None
    podcast = cfg["podcast"] if "podcast" in cfg else {}
    return {
        "profile": profile,
        "bucket": bucket,
        "region": region,
        "podcast_name": podcast.get("name", DEFAULT_FEED_TITLE),
    }


def get_feed_url() -> str | None:
    """Return the public feed URL, or None if not configured."""
    config = _load_config()
    if not config:
        return None
    return f"https://{config['bucket']}.s3.{config['region']}.amazonaws.com/{FEED_KEY}"


def is_aws_configured() -> bool:
    """Return True if boto3 is importable and AWS credentials exist for the configured profile."""
    config = _load_config()
    if not config:
        return False
    try:
        import boto3
        session = boto3.Session(profile_name=config["profile"])
        return session.get_credentials() is not None
    except Exception:
        return False


def _get_s3_client():
    config = _load_config()
    import boto3
    session = boto3.Session(profile_name=config["profile"])
    return session.client("s3", region_name=config["region"]), config


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


def _base_url(config: dict) -> str:
    return f"https://{config['bucket']}.s3.{config['region']}.amazonaws.com"


def _build_fresh_feed(config: dict) -> ET.Element:
    """Create a minimal valid podcast RSS feed."""
    ET.register_namespace("itunes", ITUNES_NS)
    ET.register_namespace("podcast", PODCAST_NS)
    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    podcast_name = config.get("podcast_name", DEFAULT_FEED_TITLE)
    base = _base_url(config)
    ET.SubElement(channel, "title").text = podcast_name
    ET.SubElement(channel, "description").text = FEED_DESCRIPTION
    ET.SubElement(channel, "link").text = f"{base}/{FEED_KEY}"
    ET.SubElement(channel, "language").text = "en"
    ET.SubElement(channel, "{%s}author" % ITUNES_NS).text = podcast_name
    ET.SubElement(channel, "{%s}image" % ITUNES_NS, {"href": f"{base}/{ARTWORK_KEY}"})
    ET.SubElement(channel, "{%s}category" % ITUNES_NS, {"text": "Technology"})
    return rss


def _fetch_existing_feed(s3, config: dict) -> ET.Element | None:
    """Download feed.xml from S3. Returns None if it doesn't exist yet."""
    import botocore.exceptions
    try:
        response = s3.get_object(Bucket=config["bucket"], Key=FEED_KEY)
        raw = response["Body"].read().decode("utf-8")
        ET.register_namespace("itunes", ITUNES_NS)
        ET.register_namespace("podcast", PODCAST_NS)
        # Deduplicate xmlns:itunes if present (older feeds had this bug)
        ns_decl = f' xmlns:itunes="{ITUNES_NS}"'
        while raw.count(ns_decl) > 1:
            idx = raw.rindex(ns_decl)
            raw = raw[:idx] + raw[idx + len(ns_decl):]
        return ET.fromstring(raw)
    except botocore.exceptions.ClientError as e:
        if e.response["Error"]["Code"] in ("NoSuchKey", "404"):
            return None
        raise


def _add_feed_item(rss: ET.Element, title: str, s3_key: str,
                   file_size: int, base_url: str,
                   duration_seconds: int | None = None,
                   source_url: str | None = None,
                   summary: str | None = None,
                   transcript_url: str | None = None,
                   voice_name: str | None = None) -> None:
    """Prepend a new <item> to the RSS channel (newest first)."""
    channel = rss.find("channel")
    enclosure_url = f"{base_url}/{s3_key}"

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
        ET.SubElement(item, "description").text = summary
        ET.SubElement(item, "{%s}summary" % ITUNES_NS).text = summary
    ET.SubElement(item, "enclosure", {
        "url": enclosure_url,
        "length": str(file_size),
        "type": "audio/x-m4a",
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
        })

    items = channel.findall("item")
    if items:
        idx = list(channel).index(items[0])
        channel.insert(idx, item)
    else:
        channel.append(item)


def find_existing_episode(url: str) -> dict | None:
    """Check if a URL was already processed by searching the RSS feed.

    Returns dict with title, audio_url, summary, feed_url, cached=True, or None.
    """
    config = _load_config()
    if not config:
        return None
    try:
        s3, config = _get_s3_client()
        rss = _fetch_existing_feed(s3, config)
    except Exception:
        return None
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


def upload_audiobook(local_path: str, title: str, source_url: str | None = None,
                     summary: str | None = None,
                     transcript_path: str | None = None,
                     voice_name: str | None = None) -> str:
    """Upload audio to S3 and update the podcast feed. Returns the public URL."""
    s3, config = _get_s3_client()
    base_url = _base_url(config)
    bucket = config["bucket"]
    podcast_name = config.get("podcast_name", DEFAULT_FEED_TITLE)
    file_size = os.path.getsize(local_path)
    filename = os.path.basename(local_path)
    s3_key = f"{AUDIOBOOKS_PREFIX}{filename}"

    s3.upload_file(local_path, bucket, s3_key, ExtraArgs={"ContentType": "audio/x-m4a"})
    audio_url = f"{base_url}/{s3_key}"

    # Upload VTT transcript if provided
    transcript_url = None
    if transcript_path and os.path.exists(transcript_path):
        vtt_filename = os.path.basename(transcript_path)
        vtt_key = f"{AUDIOBOOKS_PREFIX}{vtt_filename}"
        s3.upload_file(transcript_path, bucket, vtt_key, ExtraArgs={"ContentType": "text/vtt; charset=utf-8"})
        transcript_url = f"{base_url}/{vtt_key}"

    # Fetch or create feed, add item, re-upload
    rss = _fetch_existing_feed(s3, config) or _build_fresh_feed(config)

    # Update channel-level metadata to match current config
    channel = rss.find("channel")
    title_el = channel.find("title")
    if title_el is not None:
        title_el.text = podcast_name
    author_el = channel.find("{%s}author" % ITUNES_NS)
    if author_el is not None:
        author_el.text = podcast_name
    # Ensure artwork is present
    image_el = channel.find("{%s}image" % ITUNES_NS)
    if image_el is None:
        ET.SubElement(channel, "{%s}image" % ITUNES_NS, {"href": f"{base_url}/{ARTWORK_KEY}"})
    else:
        image_el.set("href", f"{base_url}/{ARTWORK_KEY}")

    duration = _get_duration_seconds(local_path)
    _add_feed_item(rss, title, s3_key, file_size, base_url, duration, source_url, summary,
                   transcript_url, voice_name)

    _upload_feed(s3, config, rss)

    return f"{base_url}/{FEED_KEY}"


def _upload_feed(s3, config: dict, rss: ET.Element) -> None:
    """Serialize and upload the RSS feed to S3."""
    for attr in list(rss.attrib):
        if attr.startswith("xmlns"):
            del rss.attrib[attr]
    ET.indent(rss, space="  ")
    feed_xml = '<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(rss, encoding="unicode")
    s3.put_object(Bucket=config["bucket"], Key=FEED_KEY, Body=feed_xml.encode("utf-8"),
                  ContentType="application/rss+xml")


def _s3_key_from_url(url: str, base_url: str) -> str | None:
    """Strip the base URL to get the S3 key, e.g. 'audiobooks/file.m4a'."""
    if url and url.startswith(base_url + "/"):
        return url[len(base_url) + 1:]
    return None


def _collect_item_s3_keys(item: ET.Element, base_url: str) -> list[str]:
    """Extract S3 keys for audio and transcript from a feed <item>."""
    keys = []
    enclosure = item.find("enclosure")
    if enclosure is not None:
        key = _s3_key_from_url(enclosure.get("url", ""), base_url)
        if key:
            keys.append(key)
    transcript = item.find("{%s}transcript" % PODCAST_NS)
    if transcript is not None:
        key = _s3_key_from_url(transcript.get("url", ""), base_url)
        if key:
            keys.append(key)
    return keys


def find_episode(query: str) -> dict | None:
    """Search feed by URL (exact match) or title (case-insensitive substring).

    Returns {"title", "link", "audio_url"} or None.
    """
    config = _load_config()
    if not config:
        return None
    try:
        s3, config = _get_s3_client()
        rss = _fetch_existing_feed(s3, config)
    except Exception:
        return None
    if rss is None:
        return None

    channel = rss.find("channel")
    query_lower = query.lower().strip()
    normalized_url = query.rstrip("/")

    for item in channel.findall("item"):
        link = item.findtext("link", "")
        title = item.findtext("title", "")
        # Exact URL match
        if link and link.rstrip("/") == normalized_url:
            enclosure = item.find("enclosure")
            return {
                "title": title,
                "link": link,
                "audio_url": enclosure.get("url") if enclosure is not None else None,
            }
        # Case-insensitive title substring
        if title and query_lower in title.lower():
            enclosure = item.find("enclosure")
            return {
                "title": title,
                "link": link,
                "audio_url": enclosure.get("url") if enclosure is not None else None,
            }
    return None


def list_episodes() -> list[dict]:
    """Return list of {"title", "link"} for all feed items."""
    config = _load_config()
    if not config:
        return []
    try:
        s3, config = _get_s3_client()
        rss = _fetch_existing_feed(s3, config)
    except Exception:
        return []
    if rss is None:
        return []

    channel = rss.find("channel")
    episodes = []
    for item in channel.findall("item"):
        episodes.append({
            "title": item.findtext("title", ""),
            "link": item.findtext("link", ""),
        })
    return episodes


def delete_episode(query: str) -> dict:
    """Delete a single episode matching query (URL or title substring).

    Removes from feed XML and deletes S3 files (audio + transcript).
    Returns {"title", "files_deleted"}. Raises PipelineError if not found.
    """
    from errors import PipelineError

    s3, config = _get_s3_client()
    base_url = _base_url(config)
    rss = _fetch_existing_feed(s3, config)
    if rss is None:
        raise PipelineError("No podcast feed found.")

    channel = rss.find("channel")
    query_lower = query.lower().strip()
    normalized_url = query.rstrip("/")
    matched_item = None

    for item in channel.findall("item"):
        link = item.findtext("link", "")
        title = item.findtext("title", "")
        if (link and link.rstrip("/") == normalized_url) or \
           (title and query_lower in title.lower()):
            matched_item = item
            break

    if matched_item is None:
        raise PipelineError(f"No episode found matching: {query}")

    title = matched_item.findtext("title", "")
    s3_keys = _collect_item_s3_keys(matched_item, base_url)

    channel.remove(matched_item)
    _upload_feed(s3, config, rss)

    for key in s3_keys:
        try:
            s3.delete_object(Bucket=config["bucket"], Key=key)
        except Exception:
            pass  # best-effort cleanup

    return {"title": title, "files_deleted": len(s3_keys)}


def delete_all_episodes() -> dict:
    """Remove all episodes from feed and delete all objects under audiobooks/.

    Returns {"episodes_deleted", "files_deleted"}.
    """
    s3, config = _get_s3_client()
    rss = _fetch_existing_feed(s3, config)
    if rss is None:
        rss = _build_fresh_feed(config)

    channel = rss.find("channel")
    items = channel.findall("item")
    episodes_deleted = len(items)
    for item in items:
        channel.remove(item)
    _upload_feed(s3, config, rss)

    # Delete all objects under audiobooks/ prefix
    files_deleted = 0
    bucket = config["bucket"]
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=AUDIOBOOKS_PREFIX):
        objects = page.get("Contents", [])
        if objects:
            s3.delete_objects(
                Bucket=bucket,
                Delete={"Objects": [{"Key": obj["Key"]} for obj in objects]},
            )
            files_deleted += len(objects)

    return {"episodes_deleted": episodes_deleted, "files_deleted": files_deleted}
