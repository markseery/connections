"""
License: MIT
Description: Notification skill (connections) — send email via SMTP and keep a JSON history.

Adapted from apps/agents notification_skill:
- Exposes an APIRouter (worker-loadable), not a standalone FastAPI app.
- No CryptoMiddleware/envelopes; plain JSON requests/responses.
"""

from __future__ import annotations

import base64
import json
import os
import smtplib
import ssl
import time
import uuid
from datetime import datetime, timedelta, timezone
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field
from pydantic import model_validator


router = APIRouter()

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "skills" / "notification_skill"
NOTIFICATIONS_FILE = DATA_DIR / "notifications.json"


class EmailAttachment(BaseModel):
    filename: str
    content: str  # base64
    content_type: str = "application/octet-stream"


class SendEmailRequest(BaseModel):
    # Accept either a list or a single string; also accept "recipient" alias (LLM-friendly).
    to: list[str] | str | None = None
    subject: str = Field(..., min_length=1, max_length=500)
    body: str = ""
    html_body: str | None = None
    cc: list[str] = Field(default_factory=list)
    bcc: list[str] = Field(default_factory=list)
    reply_to: str | None = None
    attachments: list[EmailAttachment] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _normalize_aliases(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        d = dict(data)
        # Common LLM aliases.
        if "to" not in d:
            if isinstance(d.get("recipient"), str):
                d["to"] = d.get("recipient")
            elif isinstance(d.get("email"), str):
                d["to"] = d.get("email")
        # Coerce string -> list.
        if isinstance(d.get("to"), str):
            d["to"] = [d["to"]] if d["to"].strip() else []
        # Validate required.
        if not d.get("to"):
            raise ValueError("to is required")
        return d

    @model_validator(mode="after")
    def _ensure_list(self) -> "SendEmailRequest":
        if isinstance(self.to, str):
            self.to = [self.to]
        if not isinstance(self.to, list) or not self.to:
            raise ValueError("to is required")
        return self

class TestEmailRequest(BaseModel):
    to: str | None = None
    include_html: bool = True

    @model_validator(mode="before")
    @classmethod
    def _normalize_test_aliases(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        d = dict(data)
        if "to" not in d:
            if isinstance(d.get("recipient"), str):
                d["to"] = d.get("recipient")
            elif isinstance(d.get("email"), str):
                d["to"] = d.get("email")
        return d


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _load_notifications() -> list[dict[str, Any]]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if NOTIFICATIONS_FILE.exists():
        try:
            raw = json.loads(NOTIFICATIONS_FILE.read_text(encoding="utf-8"))
            return raw if isinstance(raw, list) else []
        except Exception as exc:
            print(f"[notification_skill] notifications file corrupted: {exc}", flush=True)
            return []
    return []


def _save_notifications(records: list[dict[str, Any]]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    NOTIFICATIONS_FILE.write_text(json.dumps(records, indent=2, sort_keys=True), encoding="utf-8")


def _parse_throttle(value: str) -> int:
    """Parse throttle value; allow '60' or '60%'."""
    s = (value or "").strip().rstrip("%").strip()
    return int(s) if s else 60


def _smtp_config() -> dict[str, Any]:
    return {
        "sender": os.getenv("EMAIL_SENDER", ""),
        "sender_name": os.getenv("EMAIL_SENDER_NAME", "Notification Skill"),
        "password": os.getenv("EMAIL_PASSWORD", ""),
        "smtp_host": os.getenv("SMTP_HOST", "smtp.gmail.com"),
        "smtp_port": int(os.getenv("SMTP_PORT", "587")),
        "use_tls": os.getenv("SMTP_USE_TLS", "true").lower() == "true",
        "throttle_per_min": _parse_throttle(os.getenv("NOTIFICATION_THROTTLE_PER_MINUTE", "60")),
        "default_receiver": os.getenv("EMAIL_RECEIVER_DEFAULT", ""),
    }


_recipient_timestamps: dict[str, list[datetime]] = {}


def _allow_send(recipients: list[str], throttle_per_min: int) -> bool:
    if throttle_per_min <= 0:
        return True
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(minutes=1)
    for recipient in recipients:
        history = _recipient_timestamps.get(recipient, [])
        history = [ts for ts in history if ts > window_start]
        if len(history) >= throttle_per_min:
            return False
        history.append(now)
        _recipient_timestamps[recipient] = history
    return True


def _send_smtp(cfg: dict[str, Any], req: SendEmailRequest) -> tuple[bool, str | None]:
    sender = cfg["sender"]
    password = cfg["password"]
    if not sender or not password or password == "your_app_password_here":
        return False, "Email not configured. Set EMAIL_SENDER and EMAIL_PASSWORD."

    msg = MIMEMultipart("alternative" if req.html_body else "mixed")
    msg["From"] = f"{cfg['sender_name']} <{sender}>"
    msg["To"] = ", ".join(req.to)
    msg["Subject"] = req.subject
    if req.cc:
        msg["Cc"] = ", ".join(req.cc)
    if req.reply_to:
        msg["Reply-To"] = req.reply_to

    msg.attach(MIMEText(req.body or "", "plain", "utf-8"))
    if req.html_body:
        msg.attach(MIMEText(req.html_body, "html", "utf-8"))

    for att in req.attachments:
        try:
            content = base64.b64decode(att.content)
            part = MIMEBase("application", "octet-stream")
            part.set_payload(content)
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", f"attachment; filename={att.filename}")
            msg.attach(part)
        except Exception as exc:
            print(f"[notification_skill] attachment {att.filename} failed: {exc}", flush=True)
            continue

    all_recipients = req.to + req.cc + req.bcc

    # Use certifi's CA bundle so TLS verification works on macOS (Python.org builds)
    context = ssl.create_default_context()
    if not (os.getenv("SMTP_SSL_VERIFY", "true").lower() in ("false", "0", "no")):
        try:
            import certifi
            context = ssl.create_default_context(cafile=certifi.where())
        except ImportError:
            pass
    else:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE

    try:
        with smtplib.SMTP(cfg["smtp_host"], cfg["smtp_port"], timeout=20) as server:
            server.ehlo()
            if cfg["use_tls"]:
                server.starttls(context=context)
                server.ehlo()
            server.login(sender, password)
            server.sendmail(sender, all_recipients, msg.as_string())
        return True, None
    except Exception as e:
        print(f"[notification_skill] SMTP send failed: {e}", flush=True)
        return False, str(e)


@router.get("/config")
def get_config() -> dict[str, Any]:
    """View email/SMTP config (masked). Use only when user asks to check email configuration."""
    cfg = _smtp_config()
    masked = dict(cfg)
    if masked.get("password"):
        masked["password"] = "***"
    return {"summary": "Notification config (sender, throttle).", **masked}


@router.get("/notifications")
def list_notifications(status: str | None = None, limit: int = 100, offset: int = 0) -> dict[str, Any]:
    """List past email notifications. Use when user asks to list, show, or see sent emails. Query: status, limit, offset."""
    records = _load_notifications()
    if status:
        records = [r for r in records if r.get("status") == status]
    total = len(records)
    records = sorted(records, key=lambda r: r.get("created_at", ""), reverse=True)
    page = records[offset : offset + limit]
    items = [{"title": r.get("notification_id", ""), "summary": f"{r.get('subject', '')} — {r.get('status', '')}"} for r in page]
    return {"summary": f"**{total}** notifications.", "items": items, "total": total, "offset": offset, "limit": limit, "notifications": page}


@router.get("/notifications/{notification_id}")
def get_notification(notification_id: str) -> dict[str, Any]:
    """Get one notification by ID. Use when user asks for details of a specific sent email."""
    records = _load_notifications()
    for r in records:
        if r.get("notification_id") == notification_id:
            r["summary"] = f"Notification **{notification_id}**: {r.get('subject', '')} — {r.get('status', '')}"
            return r
    raise HTTPException(status_code=404, detail="Notification not found")


@router.get("/stats")
def stats() -> dict[str, Any]:
    """Notification send statistics (total, by status). Use when user asks for email stats or counts."""
    records = _load_notifications()
    by_status: dict[str, int] = {}
    for r in records:
        s = str(r.get("status", "unknown"))
        by_status[s] = by_status.get(s, 0) + 1
    total = len(records)
    return {"summary": f"**{total}** notifications (by status: {by_status}).", "total": total, "by_status": by_status}


@router.post("/send")
def send_email(body: SendEmailRequest, response: Response) -> dict[str, Any]:
    """Send an email. Body: to (required, string or list of emails), subject (required), body (optional). Use when user asks to send or email someone."""
    start = time.perf_counter()
    cfg = _smtp_config()
    if not cfg["sender"] or not cfg["password"] or cfg["password"] == "your_app_password_here":
        response.headers["X-Processing-Time-Ms"] = f"{(time.perf_counter() - start) * 1000:.1f}"
        raise HTTPException(status_code=400, detail="Email not configured. Set EMAIL_SENDER and EMAIL_PASSWORD.")

    if cfg["default_receiver"]:
        placeholders = {"user@example.com", "recipient@example.com", "test@example.com", "example@example.com"}
        body.to = [cfg["default_receiver"] if x.lower() in placeholders else x for x in body.to]

    if not _allow_send(body.to + body.cc + body.bcc, cfg["throttle_per_min"]):
        record = {
            "notification_id": str(uuid.uuid4()),
            "type": "email",
            "status": "failed",
            "recipients": body.to + body.cc + body.bcc,
            "subject": body.subject,
            "created_at": _now_iso(),
            "sent_at": None,
            "error": "throttled",
            "metadata": body.metadata,
        }
        records = _load_notifications()
        records.append(record)
        _save_notifications(records)
        response.headers["X-Processing-Time-Ms"] = f"{(time.perf_counter() - start) * 1000:.1f}"
        raise HTTPException(status_code=429, detail="Throttled")

    ok, err = _send_smtp(cfg, body)
    nid = str(uuid.uuid4())
    record = {
        "notification_id": nid,
        "type": "email",
        "status": "sent" if ok else "failed",
        "recipients": body.to + body.cc + body.bcc,
        "subject": body.subject,
        "created_at": _now_iso(),
        "sent_at": _now_iso() if ok else None,
        "error": err,
        "metadata": body.metadata,
    }
    records = _load_notifications()
    records.append(record)
    _save_notifications(records)
    response.headers["X-Processing-Time-Ms"] = f"{(time.perf_counter() - start) * 1000:.1f}"
    if not ok:
        raise HTTPException(status_code=502, detail=err or "Failed to send")
    return {"summary": "Email sent.", "notification_id": nid, "status": "sent"}


@router.post("/send/test")
def send_test(body: TestEmailRequest) -> dict[str, Any]:
    """Send a test email. Use only when user explicitly asks to send a test email."""
    req = SendEmailRequest(
        to=[body.to],
        subject="Test Email from notification_skill",
        body=f"Test email sent at {_now_iso()}",
        html_body="<p>Test email</p>" if body.include_html else None,
        metadata={"test": True},
    )
    cfg = _smtp_config()
    if not cfg["sender"] or not cfg["password"] or cfg["password"] == "your_app_password_here":
        raise HTTPException(status_code=400, detail="Email not configured. Set EMAIL_SENDER and EMAIL_PASSWORD.")
    ok, err = _send_smtp(cfg, req)
    if not ok:
        raise HTTPException(status_code=502, detail=err or "Failed to send")
    return {"summary": "Test email sent.", "status": "sent"}


def get_router() -> APIRouter:
    return router

