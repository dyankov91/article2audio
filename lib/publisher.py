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
                   transcript_url: str | None = None) -> None:
    """Prepend a new <item> to the RSS channel (newest first)."""
    channel = rss.find("channel")
    enclosure_url = f"{base_url}/{s3_key}"

    item = ET.Element("item")
    ET.SubElement(item, "title").text = title
    ET.SubElement(item, "guid", {"isPermaLink": "false"}).text = enclosure_url
    ET.SubElement(item, "pubDate").text = formatdate(usegmt=True)
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


def upload_audiobook(local_path: str, title: str, source_url: str | None = None,
                     summary: str | None = None,
                     transcript_path: str | None = None) -> str:
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
                   transcript_url)

    # Strip any existing xmlns:itunes from the root to avoid duplicates
    # (ElementTree re-adds it via register_namespace)
    for attr in list(rss.attrib):
        if attr.startswith("xmlns"):
            del rss.attrib[attr]
    ET.indent(rss, space="  ")
    feed_xml = '<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(rss, encoding="unicode")
    s3.put_object(Bucket=bucket, Key=FEED_KEY, Body=feed_xml.encode("utf-8"),
                  ContentType="application/rss+xml")

    return f"{base_url}/{FEED_KEY}"
