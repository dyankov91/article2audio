"""S3 remote backend for a2pod podcast storage."""

from backends import RemoteBackend

FEED_KEY = "feed.xml"
AUDIOBOOKS_PREFIX = "audiobooks/"


class S3Backend(RemoteBackend):
    """AWS S3 implementation of RemoteBackend."""

    def __init__(self, profile: str, bucket: str, region: str):
        self.profile = profile
        self.bucket = bucket
        self.region = region
        self._client = None

    def _get_client(self):
        if self._client is None:
            import boto3
            session = boto3.Session(profile_name=self.profile)
            self._client = session.client("s3", region_name=self.region)
        return self._client

    def get_base_url(self) -> str:
        return f"https://{self.bucket}.s3.{self.region}.amazonaws.com"

    def get_feed_url(self) -> str:
        return f"{self.get_base_url()}/{FEED_KEY}"

    def remote_key(self, filename: str) -> str:
        return f"{AUDIOBOOKS_PREFIX}{filename}"

    def upload_file(self, local_path: str, remote_key: str, content_type: str) -> str:
        s3 = self._get_client()
        s3.upload_file(local_path, self.bucket, remote_key, ExtraArgs={"ContentType": content_type})
        return f"{self.get_base_url()}/{remote_key}"

    def read_feed(self) -> str | None:
        import botocore.exceptions
        s3 = self._get_client()
        try:
            response = s3.get_object(Bucket=self.bucket, Key=FEED_KEY)
            return response["Body"].read().decode("utf-8")
        except botocore.exceptions.ClientError as e:
            if e.response["Error"]["Code"] in ("NoSuchKey", "404"):
                return None
            raise

    def write_feed(self, xml_content: str) -> None:
        s3 = self._get_client()
        s3.put_object(
            Bucket=self.bucket, Key=FEED_KEY,
            Body=xml_content.encode("utf-8"),
            ContentType="application/rss+xml",
        )

    def delete_file(self, remote_key: str) -> None:
        s3 = self._get_client()
        s3.delete_object(Bucket=self.bucket, Key=remote_key)

    def delete_files_by_prefix(self) -> int:
        s3 = self._get_client()
        files_deleted = 0
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=AUDIOBOOKS_PREFIX):
            objects = page.get("Contents", [])
            if objects:
                s3.delete_objects(
                    Bucket=self.bucket,
                    Delete={"Objects": [{"Key": obj["Key"]} for obj in objects]},
                )
                files_deleted += len(objects)
        return files_deleted
