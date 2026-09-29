"""Evidence screenshots: S3 with short-lived presigned links in AWS, local disk in dev."""

import asyncio
import time
from pathlib import Path
from typing import Protocol


class ArtifactStore(Protocol):
    async def save_jpeg(self, key: str, data: bytes) -> None: ...
    async def url_for(self, key: str) -> str: ...


class LocalArtifactStore:
    def __init__(self, root: str, public_base_url: str) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.base_url = public_base_url.rstrip("/")

    async def save_jpeg(self, key: str, data: bytes) -> None:
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(path.write_bytes, data)

    async def url_for(self, key: str) -> str:
        return f"{self.base_url}/artifacts/{key}"


class S3ArtifactStore:
    # Links are minted on read and cached. The cache matters: the UI polls every
    # second or two, and a fresh signature each time would make every frame in the
    # replay reload and flicker. Reusing a link until near expiry keeps URLs stable.
    PRESIGN_SECONDS = 3600
    REUSE_MARGIN_SECONDS = 600

    def __init__(self, bucket: str, region: str) -> None:
        import boto3
        from botocore.config import Config

        self.bucket = bucket
        self._links: dict[str, tuple[str, float]] = {}
        self._s3 = boto3.client("s3", region_name=region, config=Config(signature_version="s3v4"))

    async def save_jpeg(self, key: str, data: bytes) -> None:
        await asyncio.to_thread(
            self._s3.put_object,
            Bucket=self.bucket,
            Key=key,
            Body=data,
            ContentType="image/jpeg",
            CacheControl="private, max-age=3600",
        )

    async def url_for(self, key: str) -> str:
        cached = self._links.get(key)
        now = time.monotonic()
        if cached and cached[1] - now > self.REUSE_MARGIN_SECONDS:
            return cached[0]
        url = await asyncio.to_thread(
            self._s3.generate_presigned_url,
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=self.PRESIGN_SECONDS,
        )
        if len(self._links) > 5000:  # bounded; scans expire after a week anyway
            self._links.clear()
        self._links[key] = (url, now + self.PRESIGN_SECONDS)
        return url
