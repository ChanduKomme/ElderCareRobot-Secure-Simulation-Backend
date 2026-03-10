from __future__ import annotations

from collections import deque
from datetime import timedelta

from sqlalchemy.orm import Session

from .models import Alert, Event


def _alert_exists(db: Session, patient_id: int, severity: str, reason_codes: list[str], evidence_event_ids: list[int]) -> bool:
    existing = db.query(Alert).filter(Alert.patient_id == patient_id).all()
    normalized_reasons = sorted(reason_codes)
    normalized_evidence = sorted(evidence_event_ids)
    for alert in existing:
        if alert.severity == severity and sorted(alert.reason_codes) == normalized_reasons and sorted(alert.evidence_event_ids) == normalized_evidence:
            return True
    return False


def create_alert_if_missing(
    db: Session,
    patient_id: int,
    severity: str,
    reason_codes: list[str],
    evidence_event_ids: list[int],
) -> Alert | None:
    if _alert_exists(db, patient_id, severity, reason_codes, evidence_event_ids):
        return None
    alert = Alert(
        patient_id=patient_id,
        severity=severity,
        status="open",
    )
    alert.reason_codes = sorted(reason_codes)
    alert.evidence_event_ids = sorted(evidence_event_ids)
    db.add(alert)
    db.flush()
    return alert


def evaluate_alerts_for_patient(db: Session, patient_id: int) -> list[Alert]:
    events = (
        db.query(Event)
        .filter(Event.patient_id == patient_id)
        .order_by(Event.timestamp.asc(), Event.id.asc())
        .all()
    )
    created_alerts: list[Alert] = []

    missed_window: deque[Event] = deque()
    for event in events:
        if event.event_type == "medication_missed":
            while missed_window and event.timestamp - missed_window[0].timestamp > timedelta(hours=24):
                missed_window.popleft()
            missed_window.append(event)
            if len(missed_window) >= 2:
                alert = create_alert_if_missing(
                    db,
                    patient_id,
                    "warning",
                    ["medication_missed_twice_24h"],
                    [missed_window[-2].id, missed_window[-1].id],
                )
                if alert:
                    created_alerts.append(alert)

    for idx, event in enumerate(events):
        if event.event_type != "fall_suspected":
            continue
        resolved = False
        evidence_ids = [event.id]
        for later in events[idx + 1 :]:
            delta = later.timestamp - event.timestamp
            if delta <= timedelta(minutes=2) and later.event_type in {"normal_activity", "medication_confirmed"}:
                resolved = True
                evidence_ids.append(later.id)
                break
            if delta > timedelta(minutes=2):
                break
        if not resolved:
            alert = create_alert_if_missing(
                db,
                patient_id,
                "critical",
                ["fall_unresolved_2m"],
                evidence_ids,
            )
            if alert:
                created_alerts.append(alert)

    wandering_events = [event for event in events if event.event_type == "wandering_night"]
    no_response_events = [event for event in events if event.event_type == "no_response"]
    for idx, start_event in enumerate(wandering_events):
        for later_event in wandering_events[idx + 1 :]:
            duration = later_event.timestamp - start_event.timestamp
            if duration <= timedelta(minutes=10):
                continue
            if duration > timedelta(hours=1):
                break
            supporting_no_response = [
                event
                for event in no_response_events
                if start_event.timestamp <= event.timestamp <= later_event.timestamp
            ]
            severity = "critical" if supporting_no_response else "warning"
            reasons = ["wandering_over_10m"]
            if supporting_no_response:
                reasons.append("no_response_during_wandering")
            evidence_ids = sorted({start_event.id, later_event.id, *[event.id for event in supporting_no_response]})
            alert = create_alert_if_missing(db, patient_id, severity, reasons, evidence_ids)
            if alert:
                created_alerts.append(alert)
            break

    return created_alerts
