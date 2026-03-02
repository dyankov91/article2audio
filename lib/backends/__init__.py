"""Backend abstraction for remote podcast storage (S3, GDrive, etc.)."""

import configparser
import logging
import os
from abc import ABC, abstractmethod
from pathlib import Path

logger = logging.getLogger(__name__)

CONFIG_PATH = Path.home() / ".config" / "a2pod" / "config"


class RemoteBackend(ABC):
    """Interface for remote podcast storage backends."""

    @abstractmethod
    def get_base_url(self) -> str:
        """Base URL for constructing public file URLs."""

    @abstractmethod
    def get_feed_url(self) -> str:
        """Full public URL to the RSS feed."""

    @abstractmethod
    def upload_file(self, local_path: str, remote_key: str, content_type: str) -> str:
        """Upload a file. Returns the public URL."""

    @abstractmethod
    def read_feed(self) -> str | None:
        """Read existing feed XML as string. Returns None if not found."""

    @abstractmethod
    def write_feed(self, xml_content: str) -> None:
        """Write feed XML to remote storage."""

    @abstractmethod
    def delete_file(self, remote_key: str) -> None:
        """Delete a single file from remote storage."""

    @abstractmethod
    def delete_files_by_prefix(self) -> int:
        """Delete all episode files from remote storage. Returns count deleted."""

    def sync_episode(self, local_path: str, transcript_path: str | None,
                     title: str, file_size: int, duration: int | None,
                     source_url: str | None, summary: str | None,
                     voice_name: str | None, config: dict) -> None:
        """Upload episode files and update remote feed.

        Default implementation uses the backend's remote_key() to map filenames to keys.
        Subclasses can override for custom behavior.
        """
        from publisher import _parse_feed_xml, _build_fresh_feed, _update_channel_metadata, \
            _add_feed_item, _serialize_feed, AUDIO_CONTENT_TYPE

        remote_base = self.get_base_url()
        filename = os.path.basename(local_path)
        remote_key = self.remote_key(filename)

        self.upload_file(local_path, remote_key, AUDIO_CONTENT_TYPE)
        enclosure_url = f"{remote_base}/{remote_key}"

        transcript_url = None
        if transcript_path and os.path.exists(transcript_path):
            vtt_filename = os.path.basename(transcript_path)
            vtt_key = self.remote_key(vtt_filename)
            self.upload_file(transcript_path, vtt_key, "text/vtt; charset=utf-8")
            transcript_url = f"{remote_base}/{vtt_key}"

        raw = self.read_feed()
        rss = _parse_feed_xml(raw) if raw else _build_fresh_feed(remote_base, config)

        _update_channel_metadata(rss, remote_base, config)
        _add_feed_item(rss, title, enclosure_url, file_size, duration, source_url, summary,
                       transcript_url, voice_name)
        self.write_feed(_serialize_feed(rss))

    def remote_key(self, filename: str) -> str:
        """Map a local filename to a remote storage key. Override per backend."""
        return filename


def get_configured_backends() -> list[RemoteBackend]:
    """Read config and instantiate backends for each configured provider."""
    if not CONFIG_PATH.exists():
        return []
    cfg = configparser.ConfigParser()
    cfg.read(CONFIG_PATH)

    backends = []

    # S3 backend
    if "aws" in cfg:
        section = cfg["aws"]
        profile = section.get("profile", "")
        bucket = section.get("bucket", "")
        region = section.get("region", "")
        if profile and bucket and region:
            try:
                from backends.s3 import S3Backend
                backends.append(S3Backend(profile, bucket, region))
            except Exception as e:
                logger.warning("S3 backend unavailable: %s", e)

    return backends
