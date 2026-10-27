# Copyright (c) 2026 Ali Sibai (https://alisibai.vercel.app | https://github.com/sibaiali).
# All Rights Reserved. Protected under German Urheberrechtsgesetz (UrhG) & International Copyright Treaties.
# Unauthorized copying, redistribution, plagiarism, or commercial use is strictly prohibited.

"""Complete parking-session billing lifecycle with Decimal precision, DST safety, and idempotency."""

from __future__ import annotations

import abc
import datetime
import math
import uuid
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Any
from zoneinfo import ZoneInfo

from smartpark.config.settings import SmartParkConfig, TariffConfig
from smartpark.core.models import (
    Customer,
    ParkingSession,
    PaymentMethodType,
    PaymentRecord,
    PaymentStatus,
    SessionStatus,
    StaffRole,
    Vehicle,
)
from smartpark.database.db import DatabaseManager


def quantize_money(amount: Decimal) -> Decimal:
    return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


@dataclass
class FeeQuote:
    """Detailed, timezone-explicit fee calculation quote."""

    gross_amount: Decimal
    discount_amount: Decimal
    ev_surcharge: Decimal
    final_amount: Decimal
    currency: str
    duration_minutes: int
    grace_period_applied: bool
    daily_cap_applied: bool
    peak_minutes: int
    off_peak_minutes: int
    timezone_name: str
    breakdown: dict[str, str]


class PaymentProviderError(RuntimeError):
    """Raised when an external or simulated payment provider is unreachable."""


class PaymentProviderAdapter(abc.ABC):
    """Abstract interface for payment providers (never accepts raw PAN or CVV)."""

    @abc.abstractmethod
    def charge_token(
        self,
        idempotency_key: str,
        customer_token: str,
        amount: Decimal,
        currency: str,
    ) -> tuple[bool, str]:
        """Charge a tokenized payment method and return (succeeded, provider_ref)."""

    @abc.abstractmethod
    def refund_transaction(
        self,
        transaction_id: str,
        amount: Decimal,
        currency: str,
    ) -> tuple[bool, str]:
        """Refund a settled payment server-side."""


class SimulatedPaymentProvider(PaymentProviderAdapter):
    """Test payment provider with configurable outage and decline simulation."""

    def __init__(self) -> None:
        self.provider_outage: bool = False
        self.simulate_decline: bool = False
        self._processed_keys: dict[str, tuple[bool, str]] = {}

    def charge_token(
        self,
        idempotency_key: str,
        customer_token: str,
        amount: Decimal,
        currency: str,
    ) -> tuple[bool, str]:
        if self.provider_outage:
            raise PaymentProviderError("Simulated payment gateway outage (HTTP 503).")
        if idempotency_key in self._processed_keys:
            return self._processed_keys[idempotency_key]
        if self.simulate_decline or customer_token.endswith("DECLINE"):
            result = (False, f"SIM-DECLINED-{uuid.uuid4().hex[:8].upper()}")
        else:
            result = (True, f"SIM-SETTLED-{uuid.uuid4().hex[:8].upper()}")
        self._processed_keys[idempotency_key] = result
        return result

    def refund_transaction(
        self,
        transaction_id: str,
        amount: Decimal,
        currency: str,
    ) -> tuple[bool, str]:
        if self.provider_outage:
            raise PaymentProviderError("Simulated payment gateway outage during refund.")
        return True, f"SIM-REFUND-{uuid.uuid4().hex[:8].upper()}"


class BillingEngine:
    """Calculates tariffs across timezones/DST and manages idempotent payments & refunds."""

    def __init__(
        self,
        config: SmartParkConfig,
        db: DatabaseManager,
        provider: PaymentProviderAdapter | None = None,
    ) -> None:
        self.config = config
        self.db = db
        self.provider: PaymentProviderAdapter = provider or SimulatedPaymentProvider()

    def calculate_fee_quote(
        self,
        entry_time: datetime.datetime,
        exit_time: datetime.datetime,
        customer: Customer | None = None,
        vehicle: Vehicle | None = None,
        validated_visit: bool = False,
        used_ev_charger: bool = False,
        tariff: TariffConfig | None = None,
    ) -> FeeQuote:
        """Calculate parking fee using UTC duration and local timezone peak/off-peak windows."""
        cfg = tariff or self.config.tariff
        tz = ZoneInfo(cfg.timezone)

        if entry_time.tzinfo is None:
            entry_utc = entry_time.replace(tzinfo=datetime.timezone.utc)
        else:
            entry_utc = entry_time.astimezone(datetime.timezone.utc)

        if exit_time.tzinfo is None:
            exit_utc = exit_time.replace(tzinfo=datetime.timezone.utc)
        else:
            exit_utc = exit_time.astimezone(datetime.timezone.utc)

        if exit_utc < entry_utc:
            raise ValueError("exit_time cannot be earlier than entry_time.")

        total_seconds = (exit_utc - entry_utc).total_seconds()
        total_minutes = int(math.ceil(total_seconds / 60.0))

        # Check grace period
        if total_minutes <= cfg.grace_period_minutes:
            return FeeQuote(
                gross_amount=Decimal("0.00"),
                discount_amount=Decimal("0.00"),
                ev_surcharge=Decimal("0.00"),
                final_amount=Decimal("0.00"),
                currency=cfg.currency,
                duration_minutes=total_minutes,
                grace_period_applied=True,
                daily_cap_applied=False,
                peak_minutes=0,
                off_peak_minutes=total_minutes,
                timezone_name=cfg.timezone,
                breakdown={
                    "duration_minutes": str(total_minutes),
                    "grace_period": f"Applied (<= {cfg.grace_period_minutes} min)",
                    "final_amount": "0.00",
                },
            )

        # Slice minute-by-minute in UTC, converting each minute's timestamp to Europe/Berlin
        # This guarantees exact behavior across spring-forward and fall-back DST transitions.
        peak_minutes = 0
        off_peak_minutes = 0
        cursor = entry_utc
        for _ in range(total_minutes):
            local_dt = cursor.astimezone(tz)
            if cfg.peak_start_hour <= local_dt.hour < cfg.peak_end_hour:
                peak_minutes += 1
            else:
                off_peak_minutes += 1
            cursor += datetime.timedelta(minutes=1)

        if cfg.use_minute_granularity:
            base_per_min = cfg.minute_rate
            peak_cost = Decimal(peak_minutes) * base_per_min * cfg.peak_multiplier
            off_peak_cost = Decimal(off_peak_minutes) * base_per_min * cfg.off_peak_multiplier
            gross = quantize_money(peak_cost + off_peak_cost)
        else:
            billable_hours = Decimal(math.ceil(total_minutes / 60.0))
            peak_ratio = Decimal(peak_minutes) / Decimal(max(1, total_minutes))
            effective_mult = (
                peak_ratio * cfg.peak_multiplier
                + (Decimal("1.0") - peak_ratio) * cfg.off_peak_multiplier
            )
            gross = quantize_money(billable_hours * cfg.hourly_rate * effective_mult)

        # Apply maximum daily cap per started 24-hour window
        days_started = max(1, math.ceil(total_minutes / (24 * 60)))
        max_cap = cfg.max_daily_fee * Decimal(days_started)
        daily_cap_applied = False
        if gross > max_cap:
            gross = quantize_money(max_cap)
            daily_cap_applied = True

        # EV Charging surcharge
        ev_surcharge = Decimal("0.00")
        if used_ev_charger or (vehicle and vehicle.requires_ev_charging):
            hours_frac = Decimal(total_minutes) / Decimal(60)
            ev_surcharge = quantize_money(hours_frac * cfg.ev_charging_surcharge_per_hour)

        subtotal = gross + ev_surcharge
        discount = Decimal("0.00")
        discount_reason = "None"

        if customer and customer.has_subscription and cfg.subscription_covered:
            discount = gross  # Subscription covers parking fee; EV surcharge still applies if used
            discount_reason = "Active Monthly Subscription (100% base parking covered)"
        elif validated_visit:
            discount = quantize_money(
                gross * (cfg.validated_visit_discount_pct / Decimal("100.0"))
            )
            discount_reason = f"Validated Visit ({cfg.validated_visit_discount_pct}% off base parking)"

        final_amount = max(Decimal("0.00"), quantize_money(subtotal - discount))

        breakdown = {
            "duration_minutes": str(total_minutes),
            "peak_minutes": str(peak_minutes),
            "off_peak_minutes": str(off_peak_minutes),
            "gross_parking_fee": str(gross),
            "daily_cap_applied": str(daily_cap_applied),
            "ev_charging_surcharge": str(ev_surcharge),
            "discount_amount": str(discount),
            "discount_reason": discount_reason,
            "final_amount": str(final_amount),
            "timezone": cfg.timezone,
        }
        return FeeQuote(
            gross_amount=gross,
            discount_amount=discount,
            ev_surcharge=ev_surcharge,
            final_amount=final_amount,
            currency=cfg.currency,
            duration_minutes=total_minutes,
            grace_period_applied=False,
            daily_cap_applied=daily_cap_applied,
            peak_minutes=peak_minutes,
            off_peak_minutes=off_peak_minutes,
            timezone_name=cfg.timezone,
            breakdown=breakdown,
        )

    def process_session_payment(
        self,
        session: ParkingSession,
        exit_time: datetime.datetime | None = None,
        method: PaymentMethodType | None = None,
        idempotency_key: str | None = None,
    ) -> PaymentRecord:
        """Settle a parking session with idempotency protection and prepaid/subscription support."""
        idem_key = idempotency_key or f"IDEM-{session.session_id}"
        existing = self.db.get_payment_by_idempotency_key(idem_key)
        if existing is not None and existing.status == PaymentStatus.SUCCEEDED:
            return existing

        customer = self.db.get_customer(session.customer_id)
        vehicle = self.db.get_vehicle_by_id(session.vehicle_id)
        space = self.db.get_space(session.space_id)
        if customer is None:
            raise ValueError("Customer not found for session billing.")

        now = exit_time or session.exit_time or datetime.datetime.now(datetime.timezone.utc)
        quote = self.calculate_fee_quote(
            entry_time=session.entry_time,
            exit_time=now,
            customer=customer,
            vehicle=vehicle,
            validated_visit=session.validated_visit,
            used_ev_charger=bool(space and space.has_charger and vehicle and vehicle.requires_ev_charging),
        )

        selected_method = method
        if selected_method is None:
            if customer.has_subscription and quote.final_amount == Decimal("0.00"):
                selected_method = PaymentMethodType.SUBSCRIPTION
            elif customer.prepaid_balance >= quote.final_amount:
                selected_method = PaymentMethodType.PREPAID_WALLET
            else:
                selected_method = PaymentMethodType.SAVED_TOKEN_SIM

        tx_id = existing.transaction_id if existing else f"TX-{uuid.uuid4().hex[:10].upper()}"
        inv_num = existing.invoice_number if existing else f"INV-{datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"

        if quote.final_amount == Decimal("0.00"):
            status = PaymentStatus.SUCCEEDED
            quote.breakdown["settlement_note"] = "Zero-balance checkout (grace period or subscription)."
        elif selected_method == PaymentMethodType.PREPAID_WALLET:
            if customer.prepaid_balance >= quote.final_amount:
                customer.prepaid_balance = quantize_money(
                    customer.prepaid_balance - quote.final_amount
                )
                self.db.save_customer(customer)
                status = PaymentStatus.SUCCEEDED
                quote.breakdown["settlement_note"] = (
                    f"Deducted from prepaid wallet; remaining balance {customer.prepaid_balance} {quote.currency}."
                )
            else:
                status = PaymentStatus.FAILED
                quote.breakdown["settlement_note"] = "Insufficient prepaid wallet balance."
        else:
            try:
                ok, provider_ref = self.provider.charge_token(
                    idempotency_key=idem_key,
                    customer_token=f"TOK-{customer.public_alias}",
                    amount=quote.final_amount,
                    currency=quote.currency,
                )
                status = PaymentStatus.SUCCEEDED if ok else PaymentStatus.FAILED
                quote.breakdown["provider_ref"] = provider_ref
            except PaymentProviderError as exc:
                status = PaymentStatus.PENDING
                quote.breakdown["provider_error"] = str(exc)

        payment = PaymentRecord(
            transaction_id=tx_id,
            idempotency_key=idem_key,
            session_id=session.session_id,
            customer_id=customer.customer_id,
            amount=quote.final_amount,
            currency=quote.currency,
            method=selected_method,
            status=status,
            invoice_number=inv_num,
            breakdown=quote.breakdown,
            is_simulated=self.config.simulation_mode,
            timestamp=now,
        )
        self.db.save_payment(payment)

        session.fee_amount = quote.final_amount
        session.payment_status = status
        if status == PaymentStatus.FAILED:
            session.status = SessionStatus.CHECKOUT_PENDING
        self.db.save_session(session)
        return payment

    def refund_payment(
        self,
        transaction_id: str,
        actor_id: str,
        actor_role: StaffRole,
        reason: str = "Customer service adjustment",
    ) -> PaymentRecord:
        if actor_role not in (StaffRole.OPERATOR, StaffRole.ADMIN):
            raise PermissionError("Only OPERATOR or ADMIN roles may issue refunds.")
        payment = self.db.get_payment(transaction_id)
        if payment is None:
            raise ValueError(f"Payment {transaction_id} not found.")
        if payment.status != PaymentStatus.SUCCEEDED:
            raise ValueError(f"Only SUCCEEDED payments can be refunded (current: {payment.status.value}).")

        if payment.method == PaymentMethodType.PREPAID_WALLET:
            cust = self.db.get_customer(payment.customer_id)
            if cust:
                cust.prepaid_balance = quantize_money(cust.prepaid_balance + payment.amount)
                self.db.save_customer(cust)
        else:
            ok, ref = self.provider.refund_transaction(
                payment.transaction_id, payment.amount, payment.currency
            )
            if not ok:
                raise RuntimeError("Provider rejected refund request.")
            payment.breakdown["refund_ref"] = ref

        payment.status = PaymentStatus.REFUNDED
        payment.breakdown["refund_reason"] = reason
        self.db.save_payment(payment)

        session = self.db.get_session(payment.session_id)
        if session:
            session.payment_status = PaymentStatus.REFUNDED
            self.db.save_session(session)

        self.db.record_audit(
            actor_id=actor_id,
            actor_role=actor_role,
            action="REFUND_PAYMENT",
            target_entity=transaction_id,
            details={"amount": str(payment.amount), "reason": reason},
        )
        return payment

    def generate_receipt_text(self, payment: PaymentRecord) -> str:
        """Format a clear, privacy-preserving customer receipt."""
        lines = [
            f"=== {self.config.facility_name} ===",
            f"Invoice / Receipt : {payment.invoice_number}",
            f"Transaction ID    : {payment.transaction_id}",
            f"Session ID        : {payment.session_id}",
            f"Timestamp (UTC)   : {payment.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')}",
            f"Payment Method    : {payment.method.value}",
            f"Payment Status    : {payment.status.value}",
            "-" * 48,
        ]
        for k, v in payment.breakdown.items():
            lines.append(f"{k.replace('_', ' ').title():<24}: {v}")
        lines.extend([
            "-" * 48,
            f"TOTAL AMOUNT      : {payment.amount} {payment.currency}",
            "Note: Simulated transaction — no real card data collected.",
        ])
        return "\n".join(lines)
