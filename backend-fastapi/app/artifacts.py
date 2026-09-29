"""Evidence screenshots: S3 with short-lived presigned links in AWS, local disk in dev."""

import asyncio
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
    # Links are minted per read, so they never outlive the task role's session credentials.
    PRESIGN_SECONDS = 3600

    def __init__(self, bucket: str, region: str) -> None:
        import boto3
        from botocore.config import Config

        self.bucket = bucket
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
        return await asyncio.to_thread(
            self._s3.generate_presigned_url,
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=self.PRESIGN_SECONDS,
        )
