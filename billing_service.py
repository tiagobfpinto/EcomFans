import json
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from billing_config import (
    BILLING_CYCLE_DAYS,
    CREDIT_COSTS,
    CREDIT_PACKS,
    ERROR_REASON_BUY_CREDITS_REQUIRED,
    ERROR_REASON_PLAN_REQUIRED,
    ERROR_REASON_UPGRADE_REQUIRED,
    ESTIMATED_USAGE_COST_USD,
    PLAN_DEFINITIONS,
    PLAN_ORDER,
)
from db import ApiKey, CreditLedger, MockPayment, UsageEvent, User, db


def utc_now():
    return datetime.now(timezone.utc)


def normalize_plan_tier(plan_tier: str | None) -> str:
    if plan_tier in PLAN_DEFINITIONS:
        return plan_tier
    return "free"


def get_plan_monthly_credits(plan_tier: str) -> int:
    return int(PLAN_DEFINITIONS[normalize_plan_tier(plan_tier)]["monthly_credits"])


def get_available_credits(user: User) -> int:
    monthly = max(int(user.credits or 0), 0)
    extra = max(int(user.extra_credits or 0), 0)
    return monthly + extra


def get_credit_breakdown(user: User) -> dict:
    monthly = max(int(user.credits or 0), 0)
    extra = max(int(user.extra_credits or 0), 0)
    return {
        "monthly_credits": monthly,
        "extra_credits": extra,
        "available_credits": monthly + extra,
    }


def get_plan_rank(plan_tier: str | None) -> int:
    normalized = normalize_plan_tier(plan_tier)
    return PLAN_ORDER.index(normalized)


def has_required_plan(user: User, required_plan: str) -> bool:
    return get_plan_rank(user.plan_tier) >= get_plan_rank(required_plan)


def ensure_user_billing_defaults(user: User) -> bool:
    changed = False

    normalized_plan = normalize_plan_tier(getattr(user, "plan_tier", None))
    if getattr(user, "plan_tier", None) != normalized_plan:
        user.plan_tier = normalized_plan
        changed = True

    if getattr(user, "extra_credits", None) is None:
        user.extra_credits = 0
        changed = True

    if getattr(user, "credits", None) is None:
        user.credits = get_plan_monthly_credits(normalized_plan)
        changed = True

    if getattr(user, "next_credit_reset_at", None) is None:
        user.next_credit_reset_at = utc_now() + timedelta(days=BILLING_CYCLE_DAYS)
        changed = True

    return changed


def _json_or_none(data: dict | None) -> str | None:
    if data is None:
        return None
    return json.dumps(data)


def _add_ledger_entry(
    user: User,
    entry_type: str,
    source: str,
    monthly_delta: int = 0,
    extra_delta: int = 0,
    feature: str | None = None,
    provider: str | None = None,
    description: str | None = None,
    metadata: dict | None = None,
) -> None:
    entry = CreditLedger(
        user_id=user.id,
        entry_type=entry_type,
        source=source,
        monthly_delta=monthly_delta,
        extra_delta=extra_delta,
        monthly_balance=max(int(user.credits or 0), 0),
        extra_balance=max(int(user.extra_credits or 0), 0),
        feature=feature,
        provider=provider,
        description=description,
        metadata_json=_json_or_none(metadata),
    )
    db.session.add(entry)


def refresh_cycle_if_needed(user: User, now: datetime | None = None) -> bool:
    changed = ensure_user_billing_defaults(user)
    now = now or utc_now()

    reset_at = user.next_credit_reset_at
    if reset_at.tzinfo is None:
        reset_at = reset_at.replace(tzinfo=timezone.utc)

    renewal_count = 0
    while now >= reset_at:
        user.credits = get_plan_monthly_credits(user.plan_tier)
        reset_at += timedelta(days=BILLING_CYCLE_DAYS)
        renewal_count += 1
        changed = True

    if renewal_count:
        user.next_credit_reset_at = reset_at
        _add_ledger_entry(
            user=user,
            entry_type="credit",
            source="plan_renewal",
            monthly_delta=max(int(user.credits or 0), 0),
            extra_delta=0,
            description="Monthly plan credit renewal",
            metadata={"renewal_count": renewal_count, "plan_tier": user.plan_tier},
        )

    return changed


def build_credit_error_payload(user: User, required_credits: int, feature: str | None = None) -> tuple[int, dict]:
    available = get_available_credits(user)
    if normalize_plan_tier(user.plan_tier) == "free":
        return 402, {
            "error": "Not enough credits.",
            "reason": ERROR_REASON_UPGRADE_REQUIRED,
            "feature": feature,
            "required_credits": required_credits,
            "available_credits": available,
            "redirect_url": "/upgrade",
        }

    return 402, {
        "error": "Not enough credits.",
        "reason": ERROR_REASON_BUY_CREDITS_REQUIRED,
        "feature": feature,
        "required_credits": required_credits,
        "available_credits": available,
        "redirect_url": "/credits-store",
    }


def build_plan_required_payload(required_plan: str, feature: str | None = None) -> tuple[int, dict]:
    return 403, {
        "error": f"This feature requires the {required_plan} plan.",
        "reason": ERROR_REASON_PLAN_REQUIRED,
        "feature": feature,
        "required_plan": required_plan,
        "redirect_url": f"/upgrade?feature={feature or 'feature'}",
    }


def ensure_credits_or_error(user: User, required_credits: int, feature: str | None = None) -> tuple[int, dict] | None:
    refresh_cycle_if_needed(user)
    if get_available_credits(user) >= required_credits:
        return None
    return build_credit_error_payload(user, required_credits, feature=feature)


def quote_ai_image(variations: int) -> dict:
    variations = max(1, int(variations))
    credits = variations * CREDIT_COSTS["ai_image_variation"]
    estimated = ESTIMATED_USAGE_COST_USD["ai_image_variation"] * variations
    return {"credits": credits, "estimated_cost_usd": estimated, "units": variations}


def quote_ai_creatives(inspiration_count: int, provider: str, prompt_only: bool) -> dict:
    inspiration_count = max(1, int(inspiration_count))

    if prompt_only:
        per = CREDIT_COSTS["ai_creatives_prompt_only_inspiration"]
        estimated_per = ESTIMATED_USAGE_COST_USD["ai_creatives_prompt_only_inspiration"]
    elif provider == "both":
        per = CREDIT_COSTS["ai_creatives_both_provider_inspiration"]
        estimated_per = ESTIMATED_USAGE_COST_USD["ai_creatives_both_inspiration"]
    elif provider == "openai":
        per = CREDIT_COSTS["ai_creatives_single_provider_inspiration"]
        estimated_per = ESTIMATED_USAGE_COST_USD["ai_creatives_openai_inspiration"]
    else:
        per = CREDIT_COSTS["ai_creatives_single_provider_inspiration"]
        estimated_per = ESTIMATED_USAGE_COST_USD["ai_creatives_gemini_inspiration"]

    credits = inspiration_count * per
    estimated = estimated_per * inspiration_count
    return {
        "credits": credits,
        "estimated_cost_usd": estimated,
        "units": inspiration_count,
        "per_inspiration_credits": per,
    }


def quote_avatars(total_pairs: int) -> dict:
    total_pairs = max(1, int(total_pairs))
    credits = total_pairs * CREDIT_COSTS["avatars_pair"]
    estimated = ESTIMATED_USAGE_COST_USD["avatars_pair"] * total_pairs
    return {"credits": credits, "estimated_cost_usd": estimated, "units": total_pairs}


def consume_credits(
    user_id: int,
    required_credits: int,
    feature: str,
    provider: str | None = None,
    units: int = 1,
    estimated_cost_usd: Decimal | float = Decimal("0"),
    metadata: dict | None = None,
) -> tuple[bool, User, dict | None]:
    required_credits = int(required_credits)
    locked_user = User.query.filter_by(id=user_id).with_for_update().first()
    if not locked_user:
        raise RuntimeError("User not found while charging credits.")

    refresh_cycle_if_needed(locked_user)
    if get_available_credits(locked_user) < required_credits:
        _, payload = build_credit_error_payload(locked_user, required_credits, feature=feature)
        return False, locked_user, payload

    monthly_before = max(int(locked_user.credits or 0), 0)
    extra_before = max(int(locked_user.extra_credits or 0), 0)

    monthly_used = min(monthly_before, required_credits)
    extra_used = required_credits - monthly_used

    locked_user.credits = monthly_before - monthly_used
    locked_user.extra_credits = extra_before - extra_used

    _add_ledger_entry(
        user=locked_user,
        entry_type="debit",
        source="usage",
        monthly_delta=-monthly_used,
        extra_delta=-extra_used,
        feature=feature,
        provider=provider,
        description=f"Usage charge for {feature}",
        metadata=metadata,
    )

    usage = UsageEvent(
        user_id=locked_user.id,
        feature=feature,
        provider=provider,
        units=max(1, int(units)),
        credits_charged=required_credits,
        estimated_cost_usd=Decimal(str(estimated_cost_usd)),
        metadata_json=_json_or_none(metadata),
    )
    db.session.add(usage)
    db.session.flush()

    return True, locked_user, None


def apply_plan_purchase(user_id: int, plan_tier: str) -> tuple[bool, dict | None]:
    plan_tier = normalize_plan_tier(plan_tier)
    if plan_tier == "free":
        return False, {"error": "Invalid plan for checkout."}

    plan = PLAN_DEFINITIONS[plan_tier]
    locked_user = User.query.filter_by(id=user_id).with_for_update().first()
    if not locked_user:
        return False, {"error": "User not found."}

    refresh_cycle_if_needed(locked_user)
    locked_user.plan_tier = plan_tier
    locked_user.credits = int(plan["monthly_credits"])
    locked_user.next_credit_reset_at = utc_now() + timedelta(days=BILLING_CYCLE_DAYS)

    payment = MockPayment(
        user_id=locked_user.id,
        payment_type="plan",
        plan_tier=plan_tier,
        pack_id=None,
        amount_eur=Decimal(plan["monthly_price_eur"]),
        currency="EUR",
        status="paid",
        metadata_json=_json_or_none({"mock": True}),
    )
    db.session.add(payment)

    _add_ledger_entry(
        user=locked_user,
        entry_type="credit",
        source="plan_purchase",
        monthly_delta=int(plan["monthly_credits"]),
        extra_delta=0,
        description=f"Plan purchase: {plan_tier}",
        metadata={"payment_type": "plan", "plan_tier": plan_tier},
    )
    db.session.flush()

    return True, {
        "plan_tier": plan_tier,
        "monthly_credits": locked_user.credits,
        "extra_credits": locked_user.extra_credits,
        "next_credit_reset_at": locked_user.next_credit_reset_at.isoformat(),
    }


def apply_credit_pack_purchase(user_id: int, pack_id: str) -> tuple[bool, dict | None]:
    pack = CREDIT_PACKS.get(pack_id)
    if not pack:
        return False, {"error": "Invalid credit pack."}

    locked_user = User.query.filter_by(id=user_id).with_for_update().first()
    if not locked_user:
        return False, {"error": "User not found."}

    refresh_cycle_if_needed(locked_user)
    locked_user.extra_credits = max(int(locked_user.extra_credits or 0), 0) + int(pack["credits"])

    payment = MockPayment(
        user_id=locked_user.id,
        payment_type="credits",
        plan_tier=None,
        pack_id=pack_id,
        amount_eur=Decimal(pack["price_eur"]),
        currency="EUR",
        status="paid",
        metadata_json=_json_or_none({"mock": True}),
    )
    db.session.add(payment)

    _add_ledger_entry(
        user=locked_user,
        entry_type="credit",
        source="credit_pack_purchase",
        monthly_delta=0,
        extra_delta=int(pack["credits"]),
        description=f"Credit pack purchase: {pack_id}",
        metadata={"payment_type": "credits", "pack_id": pack_id},
    )
    db.session.flush()

    return True, {
        "pack_id": pack_id,
        "added_credits": int(pack["credits"]),
        "monthly_credits": max(int(locked_user.credits or 0), 0),
        "extra_credits": max(int(locked_user.extra_credits or 0), 0),
    }


def get_platform_api_key(service: str) -> str | None:
    key_map = {
        "gemini": "GEMINI_API_KEY",
        "openai": "OPENAI_API_KEY",
    }
    env_name = key_map.get(service)
    if not env_name:
        return None
    key = os.getenv(env_name, "").strip()
    return key or None


def get_user_api_key(user_id: int, service: str) -> str | None:
    if not user_id:
        return None
    key_row = ApiKey.query.filter_by(user_id=user_id, service=service).first()
    return key_row.api_key if key_row else None


def get_effective_api_key(user_id: int, service: str) -> str | None:
    user_key = get_user_api_key(user_id, service)
    if user_key:
        return user_key
    return get_platform_api_key(service)


def get_billing_summary(user: User) -> dict:
    plan_tier = normalize_plan_tier(user.plan_tier)
    plan = PLAN_DEFINITIONS[plan_tier]
    breakdown = get_credit_breakdown(user)

    return {
        "plan_tier": plan_tier,
        "plan_name": plan["name"],
        "avatars_enabled": bool(plan["avatars_enabled"]),
        "monthly_plan_price_eur": str(plan["monthly_price_eur"]),
        "monthly_plan_credits": int(plan["monthly_credits"]),
        "next_credit_reset_at": user.next_credit_reset_at.isoformat() if user.next_credit_reset_at else None,
        **breakdown,
    }


def list_plan_cards(current_plan: str) -> list[dict]:
    cards = []
    for plan_id in PLAN_ORDER:
        plan = PLAN_DEFINITIONS[plan_id]
        cards.append({
            "id": plan_id,
            "name": plan["name"],
            "price_eur": str(plan["monthly_price_eur"]),
            "monthly_credits": int(plan["monthly_credits"]),
            "avatars_enabled": bool(plan["avatars_enabled"]),
            "is_current": plan_id == current_plan,
        })
    return cards


def list_credit_packs() -> list[dict]:
    return [
        {
            "id": pack["id"],
            "name": pack["name"],
            "credits": int(pack["credits"]),
            "price_eur": str(pack["price_eur"]),
        }
        for pack in CREDIT_PACKS.values()
    ]
