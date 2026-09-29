"""Evidence screenshots: S3 with short-lived presigned links in AWS, local disk in dev."""

import asyncio
import shutil
import time
from pathlib import Path
from typing import Protocol


class ArtifactStore(Protocol):
    async def save_jpeg(self, key: str, data: bytes) -> None: ...
    async def url_for(self, key: str) -> str: ...
    async def delete_prefix(self, prefix: str) -> int: ...


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

    async def delete_prefix(self, prefix: str) -> int:
        target = (self.root / prefix).resolve()
        if not target.is_relative_to(self.root.resolve()) or not target.exists():
            return 0
        count = sum(1 for p in target.rglob("*") if p.is_file())
        await asyncio.to_thread(shutil.rmtree, target)
        return count


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
        # Sign for the regional endpoint. Links signed for the global s3.amazonaws.com
        # host get a 307 to the regional one for buckets outside us-east-1, and the
        # signature covers the host, so a browser following that redirect gets an error.
        self._s3 = boto3.client(
            "s3",
            region_name=region,
            endpoint_url=f"https://s3.{region}.amazonaws.com",
            config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"}),
        )

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

    async def delete_prefix(self, prefix: str) -> int:
        """Delete every object under the prefix; returns how many were removed."""
        return await asyncio.to_thread(self._delete_prefix, prefix)

    def _delete_prefix(self, prefix: str) -> int:
        removed = 0
        paginator = self._s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            keys = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
            # delete_objects takes at most 1000 keys, which is also a page's size.
            if keys:
                self._s3.delete_objects(Bucket=self.bucket, Delete={"Objects": keys, "Quiet": True})
                removed += len(keys)
            for key in keys:
                self._links.pop(key["Key"], None)
        return removed
