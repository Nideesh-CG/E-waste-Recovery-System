"""Central Metrics Service for Circular E-Waste Recovery Platform.
Calculates real-time aggregation metrics exclusively from database records.
No hardcoded fallbacks or fabricated baseline values.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from sqlalchemy import func
from models import AssessmentResult, Device, Transfer, db

DIVERTED_STATUSES = ("Processing", "Refurbishment", "Parts recovery", "Completed")


def get_platform_metrics(period: Optional[str] = None) -> Dict[str, Any]:
    """Calculate comprehensive portfolio metrics from the database with optional period filter."""
    cutoff: Optional[datetime] = None
    if period == "Last 7 days":
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    elif period == "Last 30 days":
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)

    # Base queries
    dev_q = Device.query
    trf_q = Transfer.query
    asmt_q = AssessmentResult.query

    if cutoff is not None:
        dev_q = dev_q.filter(Device.created_at >= cutoff)
        trf_q = trf_q.filter(Transfer.timestamp >= cutoff)
        asmt_q = asmt_q.filter(AssessmentResult.assessed_at >= cutoff)

    total_devices = dev_q.count()

    # Distinct separation of Pending Assessment vs Processing
    pending_assessments = dev_q.filter(Device.status == "Pending assessment").count()
    processing_devices = dev_q.filter(Device.status == "Processing").count()

    refurbishment_candidates = dev_q.filter(Device.status == "Refurbishment").count()
    parts_recovery = dev_q.filter(Device.status == "Parts recovery").count()
    completed_devices = dev_q.filter(Device.status == "Completed").count()

    # Financial and environmental metrics derived from real AssessmentResult records
    val_sum_q = db.session.query(func.coalesce(func.sum(AssessmentResult.component_value), 0.0))
    cost_sum_q = db.session.query(func.coalesce(func.sum(AssessmentResult.repair_cost), 0.0))
    co2_sum_q = db.session.query(func.coalesce(func.sum(AssessmentResult.co2_avoided_kg), 0.0))

    if cutoff is not None:
        val_sum_q = val_sum_q.filter(AssessmentResult.assessed_at >= cutoff)
        cost_sum_q = cost_sum_q.filter(AssessmentResult.assessed_at >= cutoff)
        co2_sum_q = co2_sum_q.filter(AssessmentResult.assessed_at >= cutoff)

    val_sum = val_sum_q.scalar() or 0.0
    cost_sum = cost_sum_q.scalar() or 0.0
    co2_sum = co2_sum_q.scalar() or 0.0

    assessed_count = asmt_q.count()

    # Diverted weight in kg: sum weights of devices that have entered the recovery pipeline
    # Requirement: Do not count Pending Assessment devices as diverted e-waste.
    # Use statuses: Processing, Refurbishment, Parts recovery, Completed
    diverted_devices = dev_q.filter(Device.status.in_(DIVERTED_STATUSES)).all()
    total_weight = 0.0
    for dev in diverted_devices:
        try:
            total_weight += float(dev.weight)
        except (ValueError, TypeError):
            pass

    # =========================================================================
    # Circular Recovery Rate Formula:
    # Numerator: Total devices in circular recovery pathways:
    #            (Refurbishment + Parts recovery + Completed)
    # Denominator: Total registered devices (total_devices).
    #
    # Business logic notes:
    # - Devices with status 'Pending assessment' are NOT counted as recovered.
    # - Devices with status 'Processing' (awaiting partner assessment outcome)
    #   are NOT counted as recovered until a circular pathway is determined.
    # - Rate (%) = (active_recovery / total_devices * 100.0) if total_devices > 0 else 0.0
    # =========================================================================
    active_recovery = refurbishment_candidates + parts_recovery + completed_devices
    circular_rate = (active_recovery / total_devices * 100.0) if total_devices > 0 else 0.0

    # Transfers breakdown
    transfers_total = trf_q.count()
    transfers_completed = trf_q.filter(Transfer.status == "Completed").count()
    transfers_processing = trf_q.filter(Transfer.status == "Processing").count()
    transfers_pending = trf_q.filter(Transfer.status == "Pending").count()
    transfers_failed = trf_q.filter(Transfer.status == "Failed").count()

    # Category distribution
    categories = ["Laptop", "Smartphone", "Monitor", "Tablet", "Other"]
    category_counts = {}
    for cat in categories:
        if cat == "Other":
            count = dev_q.filter(~Device.type.in_(["Laptop", "Smartphone", "Monitor", "Tablet"])).count()
        else:
            count = dev_q.filter(Device.type == cat).count()
        category_counts[cat] = count

    # Value per category
    category_values = {}
    for cat in categories:
        val_sub_q = db.session.query(
            func.coalesce(func.sum(AssessmentResult.component_value), 0.0)
        ).join(Device, Device.id == AssessmentResult.device_id)

        if cutoff is not None:
            val_sub_q = val_sub_q.filter(AssessmentResult.assessed_at >= cutoff)

        if cat == "Other":
            val = val_sub_q.filter(~Device.type.in_(["Laptop", "Smartphone", "Monitor", "Tablet"])).scalar() or 0.0
        else:
            val = val_sub_q.filter(Device.type == cat).scalar() or 0.0
        category_values[cat] = val

    # Category percentages
    category_percentages = {}
    for cat, count in category_counts.items():
        pct = (count / total_devices * 100.0) if total_devices > 0 else 0.0
        category_percentages[cat] = pct

    return {
        "period": period or "All time",
        "total_items": total_devices,
        "total_devices": total_devices,
        "pending_assessments": pending_assessments,
        "processing_devices": processing_devices,
        "processing_count": processing_devices,
        "pending_count": pending_assessments,
        "refurbishment_candidates": refurbishment_candidates,
        "refurbishment_count": refurbishment_candidates,
        "parts_recovery": parts_recovery,
        "parts_count": parts_recovery,
        "completed_count": completed_devices,
        "completed_devices": completed_devices,
        "assessed_count": assessed_count,
        "total_recovery_value": round(val_sum, 2),
        "total_repair_cost": round(cost_sum, 2),
        "total_co2_kg": round(co2_sum, 2),
        "total_weight_kg": round(total_weight, 2),
        "circular_recovery_rate": round(circular_rate, 1),
        "transfers": {
            "total": transfers_total,
            "completed": transfers_completed,
            "processing": transfers_processing,
            "pending": transfers_pending,
            "failed": transfers_failed,
        },
        "category_counts": category_counts,
        "category_values": category_values,
        "category_percentages": category_percentages,
    }
