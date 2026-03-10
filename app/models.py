from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class JsonListMixin:
    @staticmethod
    def dump_json(value: Any) -> str:
        return json.dumps(value, sort_keys=True)

    @staticmethod
    def load_json(value: str | None, default: Any):
        if not value:
            return default
        return json.loads(value)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    username: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(512))
    role: Mapped[str] = mapped_column(String(32), index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    links: Mapped[list[PatientUserLink]] = relationship(back_populates="user", cascade="all, delete-orphan")
    notes: Mapped[list[ProviderNote]] = relationship(back_populates="author")


class Patient(Base):
    __tablename__ = "patients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    external_ref: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    consent: Mapped[Consent | None] = relationship(back_populates="patient", uselist=False, cascade="all, delete-orphan")
    events: Mapped[list[Event]] = relationship(back_populates="patient", cascade="all, delete-orphan")
    alerts: Mapped[list[Alert]] = relationship(back_populates="patient", cascade="all, delete-orphan")
    links: Mapped[list[PatientUserLink]] = relationship(back_populates="patient", cascade="all, delete-orphan")
    notes: Mapped[list[ProviderNote]] = relationship(back_populates="patient", cascade="all, delete-orphan")


class PatientUserLink(Base):
    __tablename__ = "patient_user_links"
    __table_args__ = (UniqueConstraint("patient_id", "user_id", name="uq_patient_user_link"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    patient: Mapped[Patient] = relationship(back_populates="links")
    user: Mapped[User] = relationship(back_populates="links")


class Consent(Base):
    __tablename__ = "consents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), unique=True, index=True)
    location_visibility: Mapped[bool] = mapped_column(Boolean, default=False)
    interaction_visibility: Mapped[bool] = mapped_column(Boolean, default=False)
    adherence_visibility: Mapped[bool] = mapped_column(Boolean, default=True)
    incidents_visibility: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    patient: Mapped[Patient] = relationship(back_populates="consent")


class Event(Base, JsonListMixin):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    category: Mapped[str] = mapped_column(String(64), index=True)
    severity: Mapped[str] = mapped_column(String(32), index=True)
    description: Mapped[str] = mapped_column(Text)
    evidence_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    risk_score_after: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    patient: Mapped[Patient] = relationship(back_populates="events")

    @property
    def evidence(self) -> dict[str, Any] | None:
        if not self.evidence_json:
            return None
        return json.loads(self.evidence_json)

    @evidence.setter
    def evidence(self, value: dict[str, Any] | None) -> None:
        self.evidence_json = json.dumps(value, sort_keys=True) if value is not None else None


class Alert(Base, JsonListMixin):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    severity: Mapped[str] = mapped_column(String(32), index=True)
    reason_codes_json: Mapped[str] = mapped_column(Text)
    evidence_event_ids_json: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="open", index=True)
    acknowledged_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    patient: Mapped[Patient] = relationship(back_populates="alerts")

    @property
    def reason_codes(self) -> list[str]:
        return self.load_json(self.reason_codes_json, [])

    @reason_codes.setter
    def reason_codes(self, value: list[str]) -> None:
        self.reason_codes_json = self.dump_json(value)

    @property
    def evidence_event_ids(self) -> list[int]:
        return self.load_json(self.evidence_event_ids_json, [])

    @evidence_event_ids.setter
    def evidence_event_ids(self, value: list[int]) -> None:
        self.evidence_event_ids_json = self.dump_json(value)


class ProviderNote(Base):
    __tablename__ = "provider_notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), index=True)
    alert_id: Mapped[int | None] = mapped_column(ForeignKey("alerts.id"), nullable=True)
    author_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    note: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    patient: Mapped[Patient] = relationship(back_populates="notes")
    author: Mapped[User] = relationship(back_populates="notes")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    actor_role: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    endpoint: Mapped[str] = mapped_column(String(255), index=True)
    method: Mapped[str] = mapped_column(String(16), index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    patient_id: Mapped[int | None] = mapped_column(ForeignKey("patients.id"), nullable=True, index=True)
    decision: Mapped[str] = mapped_column(String(32), index=True)
    reason: Mapped[str] = mapped_column(String(255))
    http_status: Mapped[int] = mapped_column(Integer)
