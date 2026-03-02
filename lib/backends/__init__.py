"""Backend abstraction for remote podcast storage."""

import configparser
import logging
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

    @abstractmethod
    def remote_key(self, filename: str) -> str:
        """Map a local filename to a remote storage key."""


def get_active_backend() -> RemoteBackend | None:
    """Return the single active backend based on [publisher] provider config.

    Returns None for provider=local (default), or the appropriate backend instance.
    """
    if not CONFIG_PATH.exists():
        return None
    cfg = configparser.ConfigParser()
    cfg.read(CONFIG_PATH)

    provider = cfg.get("publisher", "provider", fallback="local").strip().lower()

    if provider == "local":
        return None

    if provider == "s3":
        section = cfg["aws"] if "aws" in cfg else {}
        profile = section.get("profile", "")
        bucket = section.get("bucket", "")
        region = section.get("region", "")
        if profile and bucket and region:
            try:
                from backends.s3 import S3Backend
                return S3Backend(profile, bucket, region)
            except Exception as e:
                logger.warning("S3 backend unavailable: %s", e)
        else:
            logger.warning("S3 provider selected but [aws] config incomplete")
        return None

    logger.warning("Unknown publisher provider: %s — falling back to local", provider)
    return None
