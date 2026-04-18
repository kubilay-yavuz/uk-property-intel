"""Tests for shared ``_core`` utilities."""

from __future__ import annotations

import re
from typing import Any

import httpx
import pytest
import respx
from uk_property_apis._core.base_client import BaseAPIClient
from uk_property_apis._core.exceptions import (
    AuthError,
    NotFoundError,
    RateLimitError,
    ServerError,
    UKPropertyAPIError,
    ValidationError,
)
from uk_property_apis._core.rate_limit import AsyncTokenBucket


class _ProbeClient(BaseAPIClient):
    """Minimal concrete client for exercising ``BaseAPIClient``."""

    async def probe_get(self, path: str) -> dict[str, Any]:
        return await self._get(path)


@pytest.mark.asyncio
async def test_token_bucket_acquires() -> None:
    bucket = AsyncTokenBucket(capacity=1.0, rate=10.0)
    await bucket.acquire(cost=0.1)
    await bucket.acquire(cost=0.1)


@pytest.mark.asyncio
async def test_token_bucket_invalid() -> None:
    with pytest.raises(ValueError):
        AsyncTokenBucket(capacity=0, rate=1)
    b = AsyncTokenBucket(capacity=1, rate=1)
    with pytest.raises(ValueError):
        await b.acquire(cost=0)


def test_exception_hierarchy() -> None:
    assert issubclass(AuthError, UKPropertyAPIError)
    assert issubclass(NotFoundError, UKPropertyAPIError)
    assert issubclass(RateLimitError, UKPropertyAPIError)
    assert issubclass(ServerError, UKPropertyAPIError)
    assert issubclass(ValidationError, UKPropertyAPIError)


@pytest.mark.asyncio
@respx.mock
async def test_base_client_maps_404() -> None:
    respx.get(re.compile(r"https://example\.invalid/.*")).mock(
        return_value=httpx.Response(404, json={"detail": "missing"}),
    )
    async with _ProbeClient(base_url="https://example.invalid/") as client:
        with pytest.raises(NotFoundError):
            await client.probe_get("missing")


@pytest.mark.asyncio
@respx.mock
async def test_base_client_maps_401() -> None:
    respx.get(re.compile(r"https://example\.invalid/.*")).mock(
        return_value=httpx.Response(401, json={}),
    )
    async with _ProbeClient(base_url="https://example.invalid/") as client:
        with pytest.raises(AuthError):
            await client.probe_get("secret")


@pytest.mark.asyncio
@respx.mock
async def test_base_client_invalid_json_root() -> None:
    respx.get(re.compile(r"https://example\.invalid/.*")).mock(
        return_value=httpx.Response(200, json="not-an-object"),
    )
    async with _ProbeClient(base_url="https://example.invalid/") as client:
        with pytest.raises(ValidationError):
            await client.probe_get("x")


@pytest.mark.asyncio
@respx.mock
async def test_base_client_get_list_happy() -> None:
    respx.get(re.compile(r"https://example\.invalid/.*")).mock(
        return_value=httpx.Response(200, json=[1, 2, 3]),
    )

    class _ListClient(BaseAPIClient):
        async def run(self) -> list[Any]:
            return await self._get_list("arr")

    async with _ListClient(base_url="https://example.invalid/") as client:
        assert await client.run() == [1, 2, 3]


@pytest.mark.asyncio
@respx.mock
async def test_base_client_get_list_rejects_object() -> None:
    respx.get(re.compile(r"https://example\.invalid/.*")).mock(
        return_value=httpx.Response(200, json={"a": 1}),
    )

    class _ListClient(BaseAPIClient):
        async def run(self) -> None:
            await self._get_list("arr")

    async with _ListClient(base_url="https://example.invalid/") as client:
        with pytest.raises(ValidationError):
            await client.run()
