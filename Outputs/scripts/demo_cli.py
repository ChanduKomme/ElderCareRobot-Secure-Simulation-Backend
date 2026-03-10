#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import requests

BASE_URL = os.getenv("BASE_URL", "http://127.0.0.1:8000")
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "."))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


class Api:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    def login(self, username: str, password: str) -> str:
        res = requests.post(f"{self.base_url}/auth/login", json={"username": username, "password": password}, timeout=10)
        res.raise_for_status()
        return res.json()["access_token"]

    def req(self, method: str, path: str, token: str | None = None, **kwargs):
        headers = kwargs.pop("headers", {})
        if token:
            headers["Authorization"] = f"Bearer {token}"
        res = requests.request(method, f"{self.base_url}{path}", headers=headers, timeout=20, **kwargs)
        try:
            body = res.json()
        except Exception:
            body = res.text
        return res.status_code, body



def pretty(name: str, data):
    print(f"\n=== {name} ===")
    print(json.dumps(data, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    suffix = str(int(time.time()))[-6:]
    api = Api(BASE_URL)
    admin = api.login("admin", "Admin123!")

    status, relative = api.req("POST", "/users", admin, json={"username": f"relative_demo_{suffix}", "password": "Relative123!", "role": "relative"})
    pretty("create relative", {"status": status, "body": relative})
    status, provider = api.req("POST", "/users", admin, json={"username": f"provider_demo_{suffix}", "password": "Provider123!", "role": "provider"})
    pretty("create provider", {"status": status, "body": provider})
    status, patient = api.req("POST", "/patients", admin, json={"external_ref": f"PAT-DEMO-{suffix}", "full_name": "Eleanor Rigby"})
    pretty("create patient", {"status": status, "body": patient})

    pid = patient["id"]
    api.req("POST", f"/patients/{pid}/link-user", admin, json={"user_id": relative["id"]})
    api.req("POST", f"/patients/{pid}/link-user", admin, json={"user_id": provider["id"]})
    api.req("POST", f"/patients/{pid}/consents", admin, json={
        "location_visibility": False,
        "interaction_visibility": False,
        "adherence_visibility": True,
        "incidents_visibility": True,
    })
    status, sim = api.req("POST", f"/patients/{pid}/simulate?seed=123&hours=24", admin)
    pretty("simulate", {"status": status, "body": sim})

    relative_token = api.login(relative["username"], "Relative123!")
    provider_token = api.login(provider["username"], "Provider123!")

    status, admin_alerts = api.req("GET", f"/alerts?patient_id={pid}", admin)
    pretty("admin sees all alerts", {"status": status, "count": len(admin_alerts), "body": admin_alerts})

    status, relative_status = api.req("GET", f"/patients/{pid}/status", relative_token)
    pretty("relative status shows redaction", {"status": status, "body": relative_status})

    status, relative_events = api.req("GET", f"/patients/{pid}/events?limit=20", relative_token)
    pretty("relative event view", {"status": status, "body": relative_events})

    status, relative_alerts = api.req("GET", f"/alerts?patient_id={pid}", relative_token)
    pretty("relative alert view filtered by consent", {"status": status, "count": len(relative_alerts), "body": relative_alerts})

    denied_alert_id = relative_alerts[0]["id"]
    status, denied = api.req("POST", f"/alerts/{denied_alert_id}/ack", relative_token)
    pretty("relative denied ack", {"status": status, "body": denied})

    status, provider_alerts = api.req("GET", f"/alerts?patient_id={pid}", provider_token)
    pretty("provider alert view", {"status": status, "count": len(provider_alerts), "body": provider_alerts})

    critical_alert_id = next(alert["id"] for alert in provider_alerts if alert["severity"] == "critical")
    status, ack = api.req("POST", f"/alerts/{critical_alert_id}/ack", provider_token)
    pretty("provider ack", {"status": status, "body": ack})

    status, note = api.req("POST", f"/patients/{pid}/notes", provider_token, json={"note": "Initial clinical review completed.", "alert_id": critical_alert_id})
    pretty("provider note", {"status": status, "body": note})

    status, evidence = api.req("GET", f"/alerts/{critical_alert_id}/evidence-pack", provider_token)
    pretty("evidence pack", {"status": status, "body": evidence})
    evidence_path = OUTPUT_DIR / "evidence_pack_demo.json"
    evidence_path.write_text(json.dumps(evidence, indent=2))

    status, verified = api.req("POST", "/verify-evidence-pack", json=evidence)
    pretty("verify evidence", {"status": status, "body": verified})

    status, resolved = api.req("POST", f"/alerts/{critical_alert_id}/resolve", provider_token, json={"resolution_note": "Patient contacted and clinically stable."})
    pretty("provider resolve", {"status": status, "body": resolved})

    status, logs = api.req("GET", "/audit-logs?limit=20", admin)
    pretty("audit logs", {"status": status, "body": logs})
