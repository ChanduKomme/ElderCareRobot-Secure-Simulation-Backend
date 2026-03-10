from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from .alerts import evaluate_alerts_for_patient
from .models import Event, Patient
from .rbac import EVENT_CATEGORY_BY_TYPE

SEVERITY_BY_TYPE = {
    "medication_due": "info",
    "medication_confirmed": "info",
    "medication_missed": "warning",
    "hydration_prompted": "info",
    "no_response": "warning",
    "fall_suspected": "critical",
    "wandering_night": "warning",
    "normal_activity": "info",
}

RISK_DELTA_BY_TYPE = {
    "medication_due": 2,
    "medication_confirmed": -6,
    "medication_missed": 12,
    "hydration_prompted": 1,
    "no_response": 8,
    "fall_suspected": 25,
    "wandering_night": 10,
    "normal_activity": -2,
}

ROOMS = ["bedroom", "kitchen", "hallway", "living_room", "bathroom"]
INTERACTION_LINES = [
    "Robot asked for hydration confirmation.",
    "Voice prompt issued for medication.",
    "Assistant requested verbal response.",
    "Check-in question asked by robot.",
]


def _event_payload(event_type: str, timestamp: datetime, rng: random.Random) -> dict[str, Any]:
    room = rng.choice(ROOMS)
    evidence: dict[str, Any] | None = None
    description_map = {
        "medication_due": "Medication reminder was scheduled.",
        "medication_confirmed": "Medication was confirmed by the robot dispenser.",
        "medication_missed": "Medication was missed after reminder window elapsed.",
        "hydration_prompted": "Hydration reminder was issued.",
        "no_response": "The robot received no response during check-in.",
        "fall_suspected": "A possible fall was detected by motion anomaly rules.",
        "wandering_night": "Night wandering pattern detected outside the bedroom.",
        "normal_activity": "Routine activity detected.",
    }

    if event_type in {"wandering_night", "normal_activity", "fall_suspected"}:
        evidence = {
            "room": room,
            "coordinates": {
                "x": round(rng.uniform(0.0, 10.0), 2),
                "y": round(rng.uniform(0.0, 10.0), 2),
            },
            "sensor": "uwb-anchor-1",
        }
    elif event_type in {"hydration_prompted", "no_response"}:
        evidence = {
            "interaction_excerpt": rng.choice(INTERACTION_LINES),
            "channel": "voice",
            "prompt_id": f"prompt-{timestamp.strftime('%H%M')}",
        }
    elif event_type in {"medication_due", "medication_confirmed", "medication_missed"}:
        evidence = {
            "medication_name": rng.choice(["Lisinopril", "Metformin", "Vitamin D"]),
            "dose": rng.choice(["5mg", "10mg", "500mg"]),
            "dispenser_id": "disp-01",
        }

    return {
        "category": EVENT_CATEGORY_BY_TYPE[event_type],
        "severity": SEVERITY_BY_TYPE[event_type],
        "description": description_map[event_type],
        "evidence": evidence,
    }


def generate_event_specs(seed: int, hours: int, start_time: datetime | None = None) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    start = start_time or (datetime.now(timezone.utc) - timedelta(hours=hours))
    slots = max(50, hours * 3)
    interval_minutes = max(12, int((hours * 60) / slots))

    schedule: list[tuple[int, str]] = []
    for idx in range(slots):
        schedule.append((idx, "normal_activity"))

    mandatory = {
        5: "wandering_night",
        6: "no_response",
        7: "wandering_night",
        10: "fall_suspected",
        16: "hydration_prompted",
        18: "no_response",
        24: "medication_due",
        25: "medication_missed",
        40: "medication_due",
        41: "medication_missed",
        52 if slots > 52 else slots - 4: "medication_due",
        53 if slots > 53 else slots - 3: "medication_confirmed",
    }

    for idx, event_type in mandatory.items():
        if 0 <= idx < slots:
            schedule[idx] = (idx, event_type)

    extra_slots = [12, 20, 29, 36, 45, slots - 2]
    extra_types = ["hydration_prompted", "normal_activity", "normal_activity", "hydration_prompted", "normal_activity", "normal_activity"]
    for idx, event_type in zip(extra_slots, extra_types):
        if 0 <= idx < slots:
            schedule[idx] = (idx, event_type)

    event_specs: list[dict[str, Any]] = []
    risk_score = 20.0
    for idx, event_type in schedule:
        jitter = rng.randint(0, max(0, interval_minutes // 3))
        timestamp = start + timedelta(minutes=idx * interval_minutes + jitter)
        payload = _event_payload(event_type, timestamp, rng)
        risk_score = max(0.0, min(100.0, risk_score + RISK_DELTA_BY_TYPE[event_type] + rng.uniform(-1.5, 1.5)))
        event_specs.append(
            {
                "timestamp": timestamp,
                "event_type": event_type,
                "severity": payload["severity"],
                "category": payload["category"],
                "description": payload["description"],
                "evidence": payload["evidence"],
                "risk_score_after": round(risk_score, 2),
            }
        )

    event_specs.sort(key=lambda item: item["timestamp"])
    return event_specs


def append_simulation_events(db: Session, patient: Patient, seed: int, hours: int) -> dict[str, Any]:
    specs = generate_event_specs(seed=seed, hours=hours)
    created_events: list[Event] = []
    for spec in specs:
        event = Event(
            patient_id=patient.id,
            timestamp=spec["timestamp"],
            event_type=spec["event_type"],
            category=spec["category"],
            severity=spec["severity"],
            description=spec["description"],
            risk_score_after=spec["risk_score_after"],
        )
        event.evidence = spec["evidence"]
        db.add(event)
        created_events.append(event)
    db.flush()

    created_alerts = evaluate_alerts_for_patient(db, patient.id)
    db.commit()
    return {
        "created_event_count": len(created_events),
        "created_alert_count": len(created_alerts),
        "event_types": sorted({event.event_type for event in created_events}),
    }
