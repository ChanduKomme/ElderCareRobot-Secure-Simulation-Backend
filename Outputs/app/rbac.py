from __future__ import annotations

from fastapi import HTTPException, Request, status
from sqlalchemy.orm import Session

from .models import Consent, Patient, PatientUserLink, User

ADMIN_ONLY = {"admin"}
CLINICAL_ROLES = {"admin", "provider"}
READ_ROLES = {"admin", "provider", "relative"}


EVENT_CATEGORY_BY_TYPE = {
    "medication_due": "adherence",
    "medication_confirmed": "adherence",
    "medication_missed": "adherence",
    "hydration_prompted": "interaction",
    "no_response": "interaction",
    "fall_suspected": "incidents",
    "wandering_night": "location",
    "normal_activity": "location",
}


def mark_audit(request: Request, decision: str, reason: str, patient_id: int | None = None) -> None:
    request.state.audit_decision = decision
    request.state.audit_reason = reason
    if patient_id is not None:
        request.state.audit_scope_patient_id = patient_id


def require_role(user: User, allowed_roles: set[str], request: Request) -> None:
    if user.role not in allowed_roles:
        mark_audit(request, "denied", "role_blocked")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Role not allowed")


def get_patient_or_404(patient_id: int, db: Session) -> Patient:
    patient = db.get(Patient, patient_id)
    if not patient:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Patient not found")
    return patient


def get_consent(patient: Patient) -> Consent:
    return patient.consent or Consent(
        patient_id=patient.id,
        location_visibility=False,
        interaction_visibility=False,
        adherence_visibility=True,
        incidents_visibility=True,
    )


def ensure_patient_link(user: User, patient: Patient, db: Session, request: Request) -> None:
    if user.role == "admin":
        return
    link = db.query(PatientUserLink).filter(
        PatientUserLink.patient_id == patient.id,
        PatientUserLink.user_id == user.id,
    ).first()
    if not link:
        mark_audit(request, "denied", "patient_link_blocked", patient.id)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User is not linked to this patient")


def ensure_patient_access(user: User, patient: Patient, db: Session, request: Request) -> Consent:
    ensure_patient_link(user, patient, db, request)
    request.state.audit_scope_patient_id = patient.id
    return get_consent(patient)


def consent_allows_category(consent: Consent, category: str) -> bool:
    if category == "location":
        return bool(consent.location_visibility)
    if category == "interaction":
        return bool(consent.interaction_visibility)
    if category == "adherence":
        return bool(consent.adherence_visibility)
    if category == "incidents":
        return bool(consent.incidents_visibility)
    return True


def enforce_alert_visibility(consent: Consent, reason_codes: list[str]) -> bool:
    for reason in reason_codes:
        if reason.startswith("medication") and not consent.adherence_visibility:
            return False
        if reason.startswith("fall") and not consent.incidents_visibility:
            return False
        if reason.startswith("wandering") and not consent.location_visibility:
            return False
        if reason.startswith("no_response") and not consent.interaction_visibility:
            return False
    return True
