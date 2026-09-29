"""Scan persistence: DynamoDB in AWS, a dict in local development."""

import asyncio
import json
from decimal import Decimal
from typing import Protocol

from .models import Scan


class ScanStore(Protocol):
    async def put(self, scan: Scan) -> None: ...
    async def get(self, scan_id: str) -> Scan | None: ...


class MemoryScanStore:
    def __init__(self) -> None:
        self._scans: dict[str, str] = {}

    async def put(self, scan: Scan) -> None:
        # Stored serialized so callers can never mutate a stored scan by reference.
        self._scans[scan.id] = scan.model_dump_json(by_alias=True)

    async def get(self, scan_id: str) -> Scan | None:
        raw = self._scans.get(scan_id)
        return Scan.model_validate_json(raw) if raw else None


class DynamoScanStore:
    """One item per scan, keyed by `id`, expired by DynamoDB TTL on `expiresAt`."""

    def __init__(self, table_name: str, region: str) -> None:
        import boto3

        self._table = boto3.resource("dynamodb", region_name=region).Table(table_name)

    async def put(self, scan: Scan) -> None:
        # boto3 rejects Python floats; a JSON round-trip turns them into Decimal.
        item = json.loads(scan.model_dump_json(by_alias=True), parse_float=Decimal)
        await asyncio.to_thread(self._table.put_item, Item=item)

    async def get(self, scan_id: str) -> Scan | None:
        response = await asyncio.to_thread(self._table.get_item, Key={"id": scan_id})
        item = response.get("Item")
        if not item:
            return None
        return Scan.model_validate_json(json.dumps(item, default=_from_decimal))


def _from_decimal(value: object) -> float | int:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    raise TypeError(f"Cannot serialize {type(value).__name__}")
