from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    user_id: int


class UserCreate(BaseModel):
    username: str
    password: str = Field(min_length=8)
    role: Literal["admin", "relative", "provider"]


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    role: str
    is_active: bool
    created_at: datetime


class PatientCreate(BaseModel):
    external_ref: str
    full_name: str


class PatientResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    external_ref: str
    full_name: str
    created_at: datetime


class ConsentUpdate(BaseModel):
    location_visibility: bool = False
    interaction_visibility: bool = False
    adherence_visibility: bool = True
    incidents_visibility: bool = True


class ConsentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    patient_id: int
    location_visibility: bool
    interaction_visibility: bool
    adherence_visibility: bool
    incidents_visibility: bool
    updated_at: datetime | None = None


class LinkUserRequest(BaseModel):
    user_id: int


class EventResponse(BaseModel):
    id: int
    patient_id: int
    timestamp: datetime
    event_type: str
    category: str
    severity: str
    description: str
    evidence: dict[str, Any] | None = None
    risk_score_after: float


class StatusResponse(BaseModel):
    patient_id: int
    patient_name: str
    latest_event_type: str | None
    latest_timestamp: datetime | None
    risk_score: float | None
    open_alerts: int
    assistant_summary: str | None = None
    latest_location: dict[str, Any] | None = None
    adherence_summary: dict[str, Any] | None = None
    incident_summary: dict[str, Any] | None = None


class AlertResponse(BaseModel):
    id: int
    patient_id: int
    created_at: datetime
    severity: str
    reason_codes: list[str]
    evidence_event_ids: list[int]
    status: str
    resolution_note: str | None = None


class ResolveAlertRequest(BaseModel):
    resolution_note: str = Field(min_length=3)


class ProviderNoteCreate(BaseModel):
    note: str = Field(min_length=3)
    alert_id: int | None = None


class ProviderNoteResponse(BaseModel):
    id: int
    patient_id: int
    alert_id: int | None
    author_user_id: int
    note: str
    created_at: datetime


class AuditLogResponse(BaseModel):
    id: int
    actor_user_id: int | None
    actor_role: str | None
    endpoint: str
    method: str
    timestamp: datetime
    patient_id: int | None
    decision: str
    reason: str
    http_status: int


class SimulationResponse(BaseModel):
    created_event_count: int
    created_alert_count: int
    event_types: list[str]


class EvidenceVerificationRequest(BaseModel):
    algorithm: str | None = None
    pack: dict[str, Any]
    content_hash: str
    signature: str | None = None


class EvidenceVerificationResponse(BaseModel):
    valid: bool
    reason: str
    computed_hash: str
    provided_hash: str
    signature_valid: bool | None = None
    computed_signature: str | None = None
    provided_signature: str | None = None
