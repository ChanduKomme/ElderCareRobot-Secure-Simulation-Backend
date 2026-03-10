from __future__ import annotations

import hashlib
import hmac
import json
import os
from typing import Any

from sqlalchemy.orm import Session

from .models import Alert, Event

EVIDENCE_HMAC_KEY = os.getenv("EVIDENCE_HMAC_KEY", "dev-evidence-key-change-me")


def canonical_json(data: dict[str, Any]) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_sha256(payload: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def compute_hmac_signature(content_hash: str) -> str:
    return hmac.new(EVIDENCE_HMAC_KEY.encode("utf-8"), content_hash.encode("utf-8"), hashlib.sha256).hexdigest()


def build_alert_metadata(alert: Alert) -> dict[str, Any]:
    return {
        "id": alert.id,
        "patient_id": alert.patient_id,
        "created_at": alert.created_at.isoformat(),
        "severity": alert.severity,
        "reason_codes": alert.reason_codes,
        "evidence_event_ids": alert.evidence_event_ids,
        "status": alert.status,
    }


def build_evidence_pack(db: Session, alert: Alert) -> dict[str, Any]:
    if alert.severity != "critical":
        raise ValueError("Evidence packs are only generated for critical alerts")

    events = (
        db.query(Event)
        .filter(Event.id.in_(alert.evidence_event_ids))
        .order_by(Event.timestamp.asc(), Event.id.asc())
        .all()
    )
    pack = {
        "alert": build_alert_metadata(alert),
        "events": [
            {
                "id": event.id,
                "patient_id": event.patient_id,
                "timestamp": event.timestamp.isoformat(),
                "event_type": event.event_type,
                "category": event.category,
                "severity": event.severity,
                "description": event.description,
                "evidence": event.evidence,
                "risk_score_after": event.risk_score_after,
            }
            for event in events
        ],
    }
    content_hash = compute_sha256(pack)
    signature = compute_hmac_signature(content_hash)
    return {
        "algorithm": "sha256",
        "pack": pack,
        "content_hash": content_hash,
        "signature": signature,
    }


def verify_evidence_payload(payload: dict[str, Any]) -> dict[str, Any]:
    pack = payload.get("pack")
    provided_hash = payload.get("content_hash")
    provided_signature = payload.get("signature")
    if not isinstance(pack, dict) or not isinstance(provided_hash, str):
        return {"valid": False, "reason": "Malformed evidence pack"}

    computed_hash = compute_sha256(pack)
    valid_hash = hmac.compare_digest(computed_hash, provided_hash)

    result = {
        "valid": valid_hash,
        "computed_hash": computed_hash,
        "provided_hash": provided_hash,
    }

    if provided_signature is not None:
        computed_signature = compute_hmac_signature(computed_hash)
        result["signature_valid"] = hmac.compare_digest(computed_signature, provided_signature)
        result["computed_signature"] = computed_signature
        result["provided_signature"] = provided_signature
        result["valid"] = result["valid"] and result["signature_valid"]

    if result["valid"]:
        result["reason"] = "Integrity check passed"
    else:
        result["reason"] = "Integrity check failed"
    return result
