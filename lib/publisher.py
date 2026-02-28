"""S3 upload and podcast RSS feed management for a2pod."""

import configparser
import json
import os
import subprocess
from email.utils import formatdate
from pathlib import Path
from xml.etree import ElementTree as ET

CONFIG_PATH = Path.home() / ".config" / "a2pod" / "config"
FEED_KEY = "feed.xml"
AUDIOBOOKS_PREFIX = "audiobooks/"

FEED_TITLE = "A2Pod"
FEED_DESCRIPTION = "Audiobooks converted from articles"
ITUNES_NS = "http://www.itunes.com/dtds/podcast-1.0.dtd"


def _load_config() -> dict | None:
    """Load AWS config from ~/.config/a2pod/config. Returns None if not configured."""
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
    return {"profile": profile, "bucket": bucket, "region": region}


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
    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = FEED_TITLE
    ET.SubElement(channel, "description").text = FEED_DESCRIPTION
    ET.SubElement(channel, "link").text = f"{_base_url(config)}/{FEED_KEY}"
    ET.SubElement(channel, "language").text = "en"
    ET.SubElement(channel, "{%s}author" % ITUNES_NS).text = "A2Pod"
    ET.SubElement(channel, "{%s}category" % ITUNES_NS, {"text": "Technology"})
    return rss


def _fetch_existing_feed(s3, config: dict) -> ET.Element | None:
    """Download feed.xml from S3. Returns None if it doesn't exist yet."""
    import botocore.exceptions
    try:
        response = s3.get_object(Bucket=config["bucket"], Key=FEED_KEY)
        xml_bytes = response["Body"].read()
        ET.register_namespace("itunes", ITUNES_NS)
        return ET.fromstring(xml_bytes.decode("utf-8"))
    except botocore.exceptions.ClientError as e:
        if e.response["Error"]["Code"] in ("NoSuchKey", "404"):
            return None
        raise


def _add_feed_item(rss: ET.Element, title: str, s3_key: str,
                   file_size: int, base_url: str,
                   duration_seconds: int | None = None) -> None:
    """Prepend a new <item> to the RSS channel (newest first)."""
    channel = rss.find("channel")
    enclosure_url = f"{base_url}/{s3_key}"

    item = ET.Element("item")
    ET.SubElement(item, "title").text = title
    ET.SubElement(item, "guid", {"isPermaLink": "false"}).text = enclosure_url
    ET.SubElement(item, "pubDate").text = formatdate(usegmt=True)
    ET.SubElement(item, "enclosure", {
        "url": enclosure_url,
        "length": str(file_size),
        "type": "audio/x-m4b",
    })
    if duration_seconds:
        h = duration_seconds // 3600
        m = (duration_seconds % 3600) // 60
        s = duration_seconds % 60
        ET.SubElement(item, "{%s}duration" % ITUNES_NS).text = f"{h:02d}:{m:02d}:{s:02d}"

    items = channel.findall("item")
    if items:
        idx = list(channel).index(items[0])
        channel.insert(idx, item)
    else:
        channel.append(item)


def upload_audiobook(local_path: str, title: str) -> str:
    """Upload .m4b to S3 and update the podcast feed. Returns the public URL."""
    s3, config = _get_s3_client()
    base_url = _base_url(config)
    bucket = config["bucket"]
    file_size = os.path.getsize(local_path)
    filename = os.path.basename(local_path)
    s3_key = f"{AUDIOBOOKS_PREFIX}{filename}"

    print(f"☁️  Uploading to S3 ({file_size / 1024 / 1024:.1f} MB)...")
    s3.upload_file(local_path, bucket, s3_key, ExtraArgs={"ContentType": "audio/x-m4b"})
    audio_url = f"{base_url}/{s3_key}"

    # Fetch or create feed, add item, re-upload
    rss = _fetch_existing_feed(s3, config) or _build_fresh_feed(config)
    duration = _get_duration_seconds(local_path)
    _add_feed_item(rss, title, s3_key, file_size, base_url, duration)

    ET.indent(rss, space="  ")
    feed_xml = '<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(rss, encoding="unicode")
    s3.put_object(Bucket=bucket, Key=FEED_KEY, Body=feed_xml.encode("utf-8"),
                  ContentType="application/rss+xml")

    feed_url = f"{base_url}/{FEED_KEY}"
    print(f"📡 Feed updated: {feed_url}")
    return audio_url
