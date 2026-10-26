"""SQLAlchemy models for Circular E-Waste Recovery Platform."""

from datetime import datetime, timezone
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

VALID_DEVICE_STATUSES = {
    "Pending assessment",
    "Processing",
    "Refurbishment",
    "Parts recovery",
    "Completed",
}

# Allowed status transitions strictly per lifecycle state machine
ALLOWED_TRANSITIONS = {
    "Pending assessment": {"Processing"},
    "Processing": {"Refurbishment", "Parts recovery", "Completed"},
    "Refurbishment": {"Completed"},
    "Parts recovery": {"Completed"},
    "Completed": set(),
}


class Device(db.Model):
    __tablename__ = "devices"

    id = db.Column(db.String(64), primary_key=True)
    name = db.Column(db.String(128), nullable=False)
    type = db.Column(db.String(64), nullable=False)
    brand = db.Column(db.String(64), nullable=False)
    model = db.Column(db.String(64), nullable=False)
    status = db.Column(db.String(64), nullable=False, default="Pending assessment", index=True)
    date = db.Column(db.String(32), nullable=True)
    condition = db.Column(db.String(64), nullable=False)
    year = db.Column(db.String(8), nullable=False)
    weight = db.Column(db.String(16), nullable=False)
    damage = db.Column(db.Text, nullable=True, default="")
    power = db.Column(db.String(64), nullable=True, default="")
    screen = db.Column(db.String(64), nullable=True, default="")
    battery = db.Column(db.String(64), nullable=True, default="")
    image = db.Column(db.String(256), nullable=True)
    document = db.Column(db.String(256), nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), index=True)

    # Relationships
    transfers = db.relationship("Transfer", backref="device_rel", lazy=True, cascade="all, delete-orphan")
    assessment = db.relationship("AssessmentResult", backref="device_rel", uselist=False, lazy=True, cascade="all, delete-orphan")
    files = db.relationship("DeviceFile", backref="device_rel", lazy=True, cascade="all, delete-orphan")

    def __init__(
        self,
        id=None,
        name=None,
        type=None,
        brand=None,
        model=None,
        status="Pending assessment",
        date=None,
        condition=None,
        year=None,
        weight=None,
        damage="",
        power="",
        screen="",
        battery="",
        image=None,
        document=None,
        created_at=None,
        **kwargs,
    ):
        if id is not None:
            self.id = id
        if name is not None:
            self.name = name
        if type is not None:
            self.type = type
        if brand is not None:
            self.brand = brand
        if model is not None:
            self.model = model
        if status is not None:
            self.status = status
        if date is not None:
            self.date = date
        if condition is not None:
            self.condition = condition
        if year is not None:
            self.year = str(year)
        if weight is not None:
            self.weight = str(weight)
        if damage is not None:
            self.damage = damage
        if power is not None:
            self.power = power
        if screen is not None:
            self.screen = screen
        if battery is not None:
            self.battery = battery
        if image is not None:
            self.image = image
        if document is not None:
            self.document = document
        if created_at is not None:
            self.created_at = created_at
        for k, v in kwargs.items():
            setattr(self, k, v)

    def __getitem__(self, item):
        return getattr(self, item, None)

    def get(self, item, default=None):
        val = getattr(self, item, None)
        return default if val is None else val

    def __setitem__(self, key, value):
        setattr(self, key, value)

    def can_transition_to(self, new_status: str) -> bool:
        """Check if transitioning to new_status is valid."""
        if new_status not in VALID_DEVICE_STATUSES:
            return False
        allowed = ALLOWED_TRANSITIONS.get(self.status, set())
        return new_status in allowed or new_status == self.status

    def transition_to(self, new_status: str, raise_on_error: bool = False) -> bool:
        """Safely transition device to new_status if allowed."""
        if not self.can_transition_to(new_status):
            if raise_on_error:
                raise ValueError(f"Invalid state transition from '{self.status}' to '{new_status}' for device {self.id}")
            return False
        self.status = new_status
        return True

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "type": self.type,
            "brand": self.brand,
            "model": self.model,
            "status": self.status,
            "date": self.date or (self.created_at.strftime("%b %d, %Y") if self.created_at else ""),
            "condition": self.condition,
            "year": str(self.year),
            "weight": str(self.weight),
            "damage": self.damage or "",
            "power": self.power or "",
            "screen": self.screen or "",
            "battery": self.battery or "",
            "image": self.image,
            "document": self.document,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class Transfer(db.Model):
    __tablename__ = "transfers"

    id = db.Column(db.String(64), primary_key=True)
    device_id = db.Column(db.String(64), db.ForeignKey("devices.id"), nullable=True)
    file = db.Column(db.String(128), nullable=False)
    sender = db.Column(db.String(64), nullable=False)
    receiver = db.Column(db.String(64), nullable=False)
    status = db.Column(db.String(32), nullable=False, default="Pending", index=True)
    time = db.Column(db.String(64), nullable=True)
    timestamp = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    http_status = db.Column(db.String(64), nullable=True)
    response_body = db.Column(db.Text, nullable=True)
    endpoint = db.Column(db.String(256), nullable=True)
    payload = db.Column(db.Text, nullable=True)
    retry_count = db.Column(db.Integer, default=0)
    idempotency_key = db.Column(db.String(128), unique=True, nullable=True, index=True)

    def __init__(
        self,
        id=None,
        device_id=None,
        file=None,
        sender=None,
        receiver=None,
        status="Pending",
        time=None,
        timestamp=None,
        http_status=None,
        response_body=None,
        endpoint=None,
        payload=None,
        retry_count=0,
        idempotency_key=None,
        **kwargs,
    ):
        if id is not None:
            self.id = id
        if device_id is not None:
            self.device_id = device_id
        if file is not None:
            self.file = file
        if sender is not None:
            self.sender = sender
        if receiver is not None:
            self.receiver = receiver
        if status is not None:
            self.status = status
        if time is not None:
            self.time = time
        if timestamp is not None:
            self.timestamp = timestamp
        if http_status is not None:
            self.http_status = http_status
        if response_body is not None:
            self.response_body = response_body
        if endpoint is not None:
            self.endpoint = endpoint
        if payload is not None:
            self.payload = payload
        if retry_count is not None:
            self.retry_count = retry_count
        if idempotency_key is not None:
            self.idempotency_key = idempotency_key
        for k, v in kwargs.items():
            setattr(self, k, v)

    def __getitem__(self, item):
        return getattr(self, item, None)

    def get(self, item, default=None):
        val = getattr(self, item, None)
        return default if val is None else val

    def __setitem__(self, key, value):
        setattr(self, key, value)

    def to_dict(self):
        return {
            "id": self.id,
            "device_id": self.device_id,
            "file": self.file,
            "sender": self.sender,
            "receiver": self.receiver,
            "status": self.status,
            "time": self.time or (self.timestamp.strftime("Today, %I:%M %p") if self.timestamp else ""),
            "http_status": self.http_status,
            "response_body": self.response_body,
            "endpoint": self.endpoint,
            "payload": self.payload,
            "retry_count": self.retry_count,
            "idempotency_key": self.idempotency_key,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
        }


class AssessmentResult(db.Model):
    __tablename__ = "assessment_results"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    device_id = db.Column(db.String(64), db.ForeignKey("devices.id"), unique=True, nullable=False)
    repairability_score = db.Column(db.Integer, nullable=False)
    circular_recovery_score = db.Column(db.Integer, nullable=False)
    repair_cost = db.Column(db.Float, nullable=False)
    component_value = db.Column(db.Float, nullable=False)
    recommendation = db.Column(db.String(64), nullable=False)
    co2_avoided_kg = db.Column(db.Float, nullable=False)
    assessed_by = db.Column(db.String(128), default="GreenTech Refurbishment Partner")
    notes = db.Column(db.Text, nullable=True)
    factors = db.Column(db.Text, nullable=True)
    assessed_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def __init__(
        self,
        id=None,
        device_id=None,
        repairability_score=None,
        circular_recovery_score=None,
        repair_cost=None,
        component_value=None,
        recommendation=None,
        co2_avoided_kg=None,
        assessed_by="GreenTech Refurbishment Partner",
        notes=None,
        factors=None,
        assessed_at=None,
        **kwargs,
    ):
        if id is not None:
            self.id = id
        if device_id is not None:
            self.device_id = device_id
        if repairability_score is not None:
            self.repairability_score = repairability_score
        if circular_recovery_score is not None:
            self.circular_recovery_score = circular_recovery_score
        if repair_cost is not None:
            self.repair_cost = repair_cost
        if component_value is not None:
            self.component_value = component_value
        if recommendation is not None:
            self.recommendation = recommendation
        if co2_avoided_kg is not None:
            self.co2_avoided_kg = co2_avoided_kg
        if assessed_by is not None:
            self.assessed_by = assessed_by
        if notes is not None:
            self.notes = notes
        if factors is not None:
            self.factors = factors
        if assessed_at is not None:
            self.assessed_at = assessed_at
        for k, v in kwargs.items():
            setattr(self, k, v)

    def __getitem__(self, item):
        return getattr(self, item, None)

    def get(self, item, default=None):
        val = getattr(self, item, None)
        return default if val is None else val

    def __setitem__(self, key, value):
        setattr(self, key, value)

    def to_dict(self):
        return {
            "id": self.id,
            "device_id": self.device_id,
            "repairability_score": self.repairability_score,
            "circular_recovery_score": self.circular_recovery_score,
            "repair_cost": self.repair_cost,
            "component_value": self.component_value,
            "recommendation": self.recommendation,
            "co2_avoided_kg": self.co2_avoided_kg,
            "assessed_by": self.assessed_by,
            "notes": self.notes,
            "factors": self.factors,
            "assessed_at": self.assessed_at.isoformat() if self.assessed_at else None,
        }


class DeviceFile(db.Model):
    __tablename__ = "device_files"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    device_id = db.Column(db.String(64), db.ForeignKey("devices.id"), nullable=False)
    file_type = db.Column(db.String(32), nullable=False)  # "image" or "document"
    filename = db.Column(db.String(128), nullable=False)
    original_filename = db.Column(db.String(256), nullable=False)
    storage_key = db.Column(db.String(256), unique=True, nullable=False, index=True)
    file_path = db.Column(db.String(512), nullable=False)
    content_type = db.Column(db.String(128), nullable=False)
    file_size = db.Column(db.Integer, nullable=False)
    storage_provider = db.Column(db.String(32), nullable=False, default="local")
    is_private = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def __init__(
        self,
        id=None,
        device_id=None,
        file_type=None,
        filename=None,
        original_filename=None,
        storage_key=None,
        file_path=None,
        content_type=None,
        file_size=None,
        storage_provider="local",
        is_private=True,
        created_at=None,
        **kwargs,
    ):
        if id is not None:
            self.id = id
        if device_id is not None:
            self.device_id = device_id
        if file_type is not None:
            self.file_type = file_type
        if filename is not None:
            self.filename = filename
        if original_filename is not None:
            self.original_filename = original_filename
        if storage_key is not None:
            self.storage_key = storage_key
        if file_path is not None:
            self.file_path = file_path
        self.content_type = content_type or "application/octet-stream"
        self.file_size = 0 if file_size is None else file_size
        if storage_provider is not None:
            self.storage_provider = storage_provider
        if is_private is not None:
            self.is_private = is_private
        if created_at is not None:
            self.created_at = created_at
        for k, v in kwargs.items():
            setattr(self, k, v)

    def to_dict(self):
        return {
            "id": self.id,
            "device_id": self.device_id,
            "file_type": self.file_type,
            "filename": self.filename,
            "original_filename": self.original_filename,
            "storage_key": self.storage_key,
            "content_type": self.content_type,
            "file_size": self.file_size,
            "storage_provider": self.storage_provider,
            "is_private": self.is_private,
            "url": f"/uploads/{self.storage_key}" if not self.is_private else f"/api/v1/files/{self.storage_key}",
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class Metadata(db.Model):
    __tablename__ = "metadata"

    key = db.Column(db.String(64), primary_key=True)
    value = db.Column(db.String(256), nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def __init__(
        self,
        key=None,
        value=None,
        created_at=None,
        **kwargs,
    ):
        if key is not None:
            self.key = key
        if value is not None:
            self.value = value
        if created_at is not None:
            self.created_at = created_at
        for k, v in kwargs.items():
            setattr(self, k, v)
