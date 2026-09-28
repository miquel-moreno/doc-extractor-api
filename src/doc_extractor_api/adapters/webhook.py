"""HTTP delivery of webhooks."""

import httpx


class WebhookError(Exception):
    """The receiver did not accept the webhook (network error or non-2xx answer)."""


async def deliver(
    url: str,
    body: bytes,
    headers: dict[str, str],
    *,
    timeout: float = 10.0,
    transport: httpx.AsyncBaseTransport | None = None,
) -> None:
    try:
        async with httpx.AsyncClient(timeout=timeout, transport=transport) as client:
            response = await client.post(
                url, content=body, headers={"Content-Type": "application/json", **headers}
            )
    except httpx.HTTPError as exc:
        raise WebhookError(f"could not reach {url}: {exc!r}") from exc
    if not response.is_success:
        raise WebhookError(f"{url} answered HTTP {response.status_code}")
