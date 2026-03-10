from __future__ import annotations

from collections import Counter
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from sqlalchemy import desc
from sqlalchemy.orm import Session

from .audit import audit_middleware
from .auth import CurrentUser, check_login_rate_limit, create_access_token, get_db, hash_password, verify_password
from .db import Base, SessionLocal, engine
from .evidence import build_evidence_pack, verify_evidence_payload
from .models import Alert, AuditLog, Consent, Event, Patient, PatientUserLink, ProviderNote, User
from .rbac import (
    ADMIN_ONLY,
    CLINICAL_ROLES,
    enforce_alert_visibility,
    ensure_patient_access,
    get_patient_or_404,
    get_consent,
    mark_audit,
    require_role,
)
from .schemas import (
    AlertResponse,
    AuditLogResponse,
    ConsentResponse,
    ConsentUpdate,
    EvidenceVerificationRequest,
    EvidenceVerificationResponse,
    EventResponse,
    LinkUserRequest,
    LoginRequest,
    PatientCreate,
    PatientResponse,
    ProviderNoteCreate,
    ProviderNoteResponse,
    ResolveAlertRequest,
    SimulationResponse,
    StatusResponse,
    TokenResponse,
    UserCreate,
    UserResponse,
)
from .simulation import append_simulation_events


def seed_default_admin() -> None:
    db = SessionLocal()
    try:
        admin = db.query(User).filter(User.username == "admin").first()
        if not admin:
            db.add(User(username="admin", password_hash=hash_password("Admin123!"), role="admin", is_active=True))
            db.commit()
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    seed_default_admin()
    yield


app = FastAPI(
    title="ElderCareRobot Secure Simulation Backend",
    version="1.0.0",
    description="Security-focused backend prototype with RBAC, consent controls, audit logging, and evidence pack integrity.",
    lifespan=lifespan,
)
app.middleware("http")(audit_middleware)



def _serialize_consent(consent: Consent) -> ConsentResponse:
    return ConsentResponse(
        patient_id=consent.patient_id,
        location_visibility=consent.location_visibility,
        interaction_visibility=consent.interaction_visibility,
        adherence_visibility=consent.adherence_visibility,
        incidents_visibility=consent.incidents_visibility,
        updated_at=consent.updated_at,
    )



def _event_visible_to_user(event: Event, user: User, consent: Consent) -> bool:
    if user.role == "admin":
        return True
    if event.category == "location":
        return bool(consent.location_visibility)
    if event.category == "interaction":
        return bool(consent.interaction_visibility)
    if event.category == "adherence":
        return bool(consent.adherence_visibility)
    if event.category == "incidents":
        return bool(consent.incidents_visibility)
    return True



def _redact_evidence(evidence: dict | None, user: User, consent: Consent) -> dict | None:
    if evidence is None or user.role == "admin":
        return evidence
    redacted = dict(evidence)
    if not consent.location_visibility:
        redacted.pop("room", None)
        redacted.pop("coordinates", None)
    if not consent.interaction_visibility:
        redacted.pop("interaction_excerpt", None)
    return redacted



def _serialize_event(event: Event, user: User, consent: Consent) -> EventResponse:
    return EventResponse(
        id=event.id,
        patient_id=event.patient_id,
        timestamp=event.timestamp,
        event_type=event.event_type,
        category=event.category,
        severity=event.severity,
        description=event.description,
        evidence=_redact_evidence(event.evidence, user, consent),
        risk_score_after=event.risk_score_after,
    )



def _build_status(patient: Patient, visible_events: list[Event], user: User, consent: Consent, db: Session) -> StatusResponse:
    latest_event = visible_events[-1] if visible_events else None
    alerts = db.query(Alert).filter(Alert.patient_id == patient.id, Alert.status != "resolved").all()
    open_alerts = len([alert for alert in alerts if user.role == "admin" or enforce_alert_visibility(consent, alert.reason_codes)])

    adherence_events = [event for event in visible_events if event.category == "adherence"]
    incident_events = [event for event in visible_events if event.category in {"incidents", "location"}]
    interaction_events = [event for event in visible_events if event.category == "interaction"]
    location_events = [event for event in visible_events if event.category == "location"]

    assistant_summary = None
    latest_location = None
    adherence_summary = None
    incident_summary = None

    if user.role == "admin" or consent.interaction_visibility:
        if interaction_events:
            assistant_summary = (
                f"{len(interaction_events)} interaction events observed; latest event was {interaction_events[-1].event_type} "
                f"at {interaction_events[-1].timestamp.isoformat()}."
            )

    if user.role == "admin" or consent.location_visibility:
        if location_events and location_events[-1].evidence:
            latest_location = _redact_evidence(location_events[-1].evidence, user, consent)

    if user.role == "admin" or consent.adherence_visibility:
        adherence_counts = Counter(event.event_type for event in adherence_events)
        adherence_summary = {
            "medication_due": adherence_counts.get("medication_due", 0),
            "medication_confirmed": adherence_counts.get("medication_confirmed", 0),
            "medication_missed": adherence_counts.get("medication_missed", 0),
        }

    if user.role == "admin" or consent.incidents_visibility or consent.location_visibility:
        incident_counts = Counter(event.event_type for event in incident_events)
        incident_summary = dict(incident_counts)

    return StatusResponse(
        patient_id=patient.id,
        patient_name=patient.full_name,
        latest_event_type=latest_event.event_type if latest_event else None,
        latest_timestamp=latest_event.timestamp if latest_event else None,
        risk_score=latest_event.risk_score_after if latest_event else None,
        open_alerts=open_alerts,
        assistant_summary=assistant_summary,
        latest_location=latest_location,
        adherence_summary=adherence_summary,
        incident_summary=incident_summary,
    )



def _serialize_alert(alert: Alert) -> AlertResponse:
    return AlertResponse(
        id=alert.id,
        patient_id=alert.patient_id,
        created_at=alert.created_at,
        severity=alert.severity,
        reason_codes=alert.reason_codes,
        evidence_event_ids=alert.evidence_event_ids,
        status=alert.status,
        resolution_note=alert.resolution_note,
    )



def _get_alert_for_user(alert_id: int, db: Session, user: User, request: Request) -> tuple[Alert, Consent]:
    alert = db.get(Alert, alert_id)
    if not alert:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alert not found")
    patient = get_patient_or_404(alert.patient_id, db)
    consent = ensure_patient_access(user, patient, db, request)
    if user.role != "admin" and not enforce_alert_visibility(consent, alert.reason_codes):
        mark_audit(request, "denied", "consent_blocked", patient.id)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Consent blocks access to this alert")
    return alert, consent


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/auth/login", response_model=TokenResponse, tags=["auth"])
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)):
    check_login_rate_limit(request)
    user = db.query(User).filter(User.username == payload.username).first()
    if not user or not verify_password(payload.password, user.password_hash):
        request.state.audit_reason = "invalid_credentials"
        request.state.audit_decision = "denied"
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    request.state.authenticated_user = user
    request.state.audit_reason = "login_success"
    request.state.audit_decision = "allowed"
    token = create_access_token(user)
    return TokenResponse(access_token=token, role=user.role, user_id=user.id)


@app.post("/users", response_model=UserResponse, status_code=status.HTTP_201_CREATED, tags=["admin"])
def create_user(payload: UserCreate, request: Request, user: CurrentUser, db: Session = Depends(get_db)):
    require_role(user, ADMIN_ONLY, request)
    existing = db.query(User).filter(User.username == payload.username).first()
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Username already exists")
    created = User(username=payload.username, password_hash=hash_password(payload.password), role=payload.role, is_active=True)
    db.add(created)
    db.commit()
    db.refresh(created)
    return created


@app.post("/patients", response_model=PatientResponse, status_code=status.HTTP_201_CREATED, tags=["admin"])
def create_patient(payload: PatientCreate, request: Request, user: CurrentUser, db: Session = Depends(get_db)):
    require_role(user, ADMIN_ONLY, request)
    existing = db.query(Patient).filter(Patient.external_ref == payload.external_ref).first()
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Patient external_ref already exists")
    patient = Patient(external_ref=payload.external_ref, full_name=payload.full_name)
    db.add(patient)
    db.flush()
    consent = Consent(
        patient_id=patient.id,
        location_visibility=False,
        interaction_visibility=False,
        adherence_visibility=True,
        incidents_visibility=True,
    )
    db.add(consent)
    db.commit()
    db.refresh(patient)
    return patient


@app.post("/patients/{patient_id}/consents", response_model=ConsentResponse, tags=["admin"])
def upsert_consent(
    patient_id: int,
    payload: ConsentUpdate,
    request: Request,
    user: CurrentUser,
    db: Session = Depends(get_db),
):
    require_role(user, ADMIN_ONLY, request)
    patient = get_patient_or_404(patient_id, db)
    request.state.audit_scope_patient_id = patient.id
    consent = patient.consent or Consent(patient_id=patient.id)
    consent.location_visibility = payload.location_visibility
    consent.interaction_visibility = payload.interaction_visibility
    consent.adherence_visibility = payload.adherence_visibility
    consent.incidents_visibility = payload.incidents_visibility
    db.add(consent)
    db.commit()
    db.refresh(consent)
    return _serialize_consent(consent)


@app.post("/patients/{patient_id}/link-user", status_code=status.HTTP_201_CREATED, tags=["admin"])
def link_user_to_patient(
    patient_id: int,
    payload: LinkUserRequest,
    request: Request,
    user: CurrentUser,
    db: Session = Depends(get_db),
):
    require_role(user, ADMIN_ONLY, request)
    patient = get_patient_or_404(patient_id, db)
    linked_user = db.get(User, payload.user_id)
    if not linked_user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    request.state.audit_scope_patient_id = patient.id
    existing = (
        db.query(PatientUserLink)
        .filter(PatientUserLink.patient_id == patient.id, PatientUserLink.user_id == linked_user.id)
        .first()
    )
    if existing:
        return {"message": "User already linked", "patient_id": patient.id, "user_id": linked_user.id}
    link = PatientUserLink(patient_id=patient.id, user_id=linked_user.id)
    db.add(link)
    db.commit()
    return {"message": "User linked", "patient_id": patient.id, "user_id": linked_user.id}


@app.post("/patients/{patient_id}/simulate", response_model=SimulationResponse, tags=["simulation"])
def simulate_events(
    patient_id: int,
    request: Request,
    user: CurrentUser,
    seed: int = Query(123, ge=0),
    hours: int = Query(24, ge=1, le=72),
    db: Session = Depends(get_db),
):
    require_role(user, ADMIN_ONLY, request)
    patient = get_patient_or_404(patient_id, db)
    request.state.audit_scope_patient_id = patient.id
    result = append_simulation_events(db, patient, seed=seed, hours=hours)
    return SimulationResponse(**result)


@app.get("/patients/{patient_id}/events", response_model=list[EventResponse], tags=["patients"])
def list_patient_events(
    patient_id: int,
    request: Request,
    user: CurrentUser,
    limit: int = Query(200, ge=1, le=500),
    db: Session = Depends(get_db),
):
    patient = get_patient_or_404(patient_id, db)
    consent = ensure_patient_access(user, patient, db, request)
    events = (
        db.query(Event)
        .filter(Event.patient_id == patient.id)
        .order_by(Event.timestamp.asc(), Event.id.asc())
        .limit(limit)
        .all()
    )
    visible = [event for event in events if _event_visible_to_user(event, user, consent)]
    return [_serialize_event(event, user, consent) for event in visible]


@app.get("/patients/{patient_id}/status", response_model=StatusResponse, tags=["patients"])
def patient_status(patient_id: int, request: Request, user: CurrentUser, db: Session = Depends(get_db)):
    patient = get_patient_or_404(patient_id, db)
    consent = ensure_patient_access(user, patient, db, request)
    events = (
        db.query(Event)
        .filter(Event.patient_id == patient.id)
        .order_by(Event.timestamp.asc(), Event.id.asc())
        .all()
    )
    visible = [event for event in events if _event_visible_to_user(event, user, consent)]
    return _build_status(patient, visible, user, consent, db)


@app.get("/alerts", response_model=list[AlertResponse], tags=["alerts"])
def list_alerts(
    request: Request,
    user: CurrentUser,
    patient_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
):
    query = db.query(Alert).order_by(desc(Alert.created_at), desc(Alert.id))
    linked_patient_ids: set[int] | None = None

    if user.role != "admin":
        linked_patient_ids = {link.patient_id for link in user.links}
        query = query.filter(Alert.patient_id.in_(linked_patient_ids or {-1}))

    if patient_id is not None:
        patient = get_patient_or_404(patient_id, db)
        consent = ensure_patient_access(user, patient, db, request)
        query = query.filter(Alert.patient_id == patient.id)
        alerts = query.all()
        return [_serialize_alert(alert) for alert in alerts if user.role == "admin" or enforce_alert_visibility(consent, alert.reason_codes)]

    alerts = query.all()
    serialized: list[AlertResponse] = []
    for alert in alerts:
        if user.role == "admin":
            serialized.append(_serialize_alert(alert))
            continue
        patient = db.get(Patient, alert.patient_id)
        if not patient or alert.patient_id not in (linked_patient_ids or set()):
            continue
        consent = get_consent(patient)
        if enforce_alert_visibility(consent, alert.reason_codes):
            serialized.append(_serialize_alert(alert))
    return serialized


@app.post("/alerts/{alert_id}/ack", response_model=AlertResponse, tags=["alerts"])
def acknowledge_alert(alert_id: int, request: Request, user: CurrentUser, db: Session = Depends(get_db)):
    require_role(user, CLINICAL_ROLES, request)
    alert, _ = _get_alert_for_user(alert_id, db, user, request)
    if alert.status == "resolved":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Resolved alert cannot be acknowledged")
    alert.status = "ack"
    alert.acknowledged_by_user_id = user.id
    alert.acknowledged_at = datetime.now(timezone.utc)
    db.add(alert)
    db.commit()
    db.refresh(alert)
    return _serialize_alert(alert)


@app.post("/alerts/{alert_id}/resolve", response_model=AlertResponse, tags=["alerts"])
def resolve_alert(
    alert_id: int,
    payload: ResolveAlertRequest,
    request: Request,
    user: CurrentUser,
    db: Session = Depends(get_db),
):
    require_role(user, CLINICAL_ROLES, request)
    alert, _ = _get_alert_for_user(alert_id, db, user, request)
    alert.status = "resolved"
    alert.resolved_by_user_id = user.id
    alert.resolved_at = datetime.now(timezone.utc)
    alert.resolution_note = payload.resolution_note
    db.add(alert)
    db.commit()
    db.refresh(alert)
    return _serialize_alert(alert)


@app.post("/patients/{patient_id}/notes", response_model=ProviderNoteResponse, status_code=status.HTTP_201_CREATED, tags=["patients"])
def add_provider_note(
    patient_id: int,
    payload: ProviderNoteCreate,
    request: Request,
    user: CurrentUser,
    db: Session = Depends(get_db),
):
    require_role(user, CLINICAL_ROLES, request)
    patient = get_patient_or_404(patient_id, db)
    ensure_patient_access(user, patient, db, request)
    if payload.alert_id is not None:
        alert = db.get(Alert, payload.alert_id)
        if not alert or alert.patient_id != patient.id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Alert does not belong to this patient")
    note = ProviderNote(patient_id=patient.id, alert_id=payload.alert_id, author_user_id=user.id, note=payload.note)
    db.add(note)
    db.commit()
    db.refresh(note)
    return ProviderNoteResponse(
        id=note.id,
        patient_id=note.patient_id,
        alert_id=note.alert_id,
        author_user_id=note.author_user_id,
        note=note.note,
        created_at=note.created_at,
    )


@app.get("/patients/{patient_id}/notes", response_model=list[ProviderNoteResponse], tags=["patients"])
def list_provider_notes(patient_id: int, request: Request, user: CurrentUser, db: Session = Depends(get_db)):
    require_role(user, CLINICAL_ROLES, request)
    patient = get_patient_or_404(patient_id, db)
    ensure_patient_access(user, patient, db, request)
    notes = (
        db.query(ProviderNote)
        .filter(ProviderNote.patient_id == patient.id)
        .order_by(ProviderNote.created_at.asc(), ProviderNote.id.asc())
        .all()
    )
    return [
        ProviderNoteResponse(
            id=note.id,
            patient_id=note.patient_id,
            alert_id=note.alert_id,
            author_user_id=note.author_user_id,
            note=note.note,
            created_at=note.created_at,
        )
        for note in notes
    ]


@app.get("/alerts/{alert_id}/evidence-pack", tags=["alerts"])
def get_evidence_pack(alert_id: int, request: Request, user: CurrentUser, db: Session = Depends(get_db)):
    alert, _ = _get_alert_for_user(alert_id, db, user, request)
    if alert.severity != "critical":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Evidence pack available only for critical alerts")
    payload = build_evidence_pack(db, alert)
    if user.role != "admin":
        for event in payload["pack"]["events"]:
            event["evidence"] = _redact_evidence(event.get("evidence"), user, get_consent(get_patient_or_404(alert.patient_id, db)))
        from .evidence import compute_sha256, compute_hmac_signature
        payload["content_hash"] = compute_sha256(payload["pack"])
        payload["signature"] = compute_hmac_signature(payload["content_hash"])
    return payload


@app.post("/verify-evidence-pack", response_model=EvidenceVerificationResponse, tags=["alerts"])
def verify_evidence_pack(payload: EvidenceVerificationRequest):
    result = verify_evidence_payload(payload.model_dump())
    return EvidenceVerificationResponse(**result)


@app.get("/audit-logs", response_model=list[AuditLogResponse], tags=["admin"])
def audit_logs(
    request: Request,
    user: CurrentUser,
    limit: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    require_role(user, ADMIN_ONLY, request)
    logs = db.query(AuditLog).order_by(desc(AuditLog.timestamp), desc(AuditLog.id)).limit(limit).all()
    return [
        AuditLogResponse(
            id=log.id,
            actor_user_id=log.actor_user_id,
            actor_role=log.actor_role,
            endpoint=log.endpoint,
            method=log.method,
            timestamp=log.timestamp,
            patient_id=log.patient_id,
            decision=log.decision,
            reason=log.reason,
            http_status=log.http_status,
        )
        for log in logs
    ]
