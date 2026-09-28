"""Webhook payload and signature.

The receiver (for example n8n) recomputes HMAC-SHA256 of the raw body with the
shared secret and compares it with the X-Webhook-Signature header: if they
match, the call really comes from this service and the body was not changed.
"""

import hashlib
import hmac
import json
from datetime import UTC, datetime
from typing import Any

from doc_extractor_api.schemas import JobOut

EVENT_JOB_FINISHED = "job.finished"
SIGNATURE_HEADER = "X-Webhook-Signature"


def build_payload(job: JobOut, *, sent_at: datetime | None = None) -> dict[str, Any]:
    return {
        "event": EVENT_JOB_FINISHED,
        "sent_at": (sent_at or datetime.now(UTC)).isoformat(),
        "job": job.model_dump(mode="json"),
    }


def encode(payload: dict[str, Any]) -> bytes:
    # Compact and stable: the signature is computed over exactly these bytes.
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def sign(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def verify(body: bytes, secret: str, signature: str) -> bool:
    """What a receiver does. compare_digest avoids timing attacks."""
    return hmac.compare_digest(sign(body, secret), signature)
