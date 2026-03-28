"""
Billing routes — Stripe Checkout + webhook integration.

Environment variables required for Stripe:
  STRIPE_SECRET_KEY        — your sk_live_... or sk_test_... key
  STRIPE_PUBLISHABLE_KEY   — your pk_live_... or pk_test_... key
  STRIPE_WEBHOOK_SECRET    — whsec_... from the Stripe dashboard webhook page

Stripe Price IDs (create in Stripe dashboard, then set here):
  STRIPE_PRICE_PRO_MONTHLY    — e.g. price_1...
  STRIPE_PRICE_ULTRA_MONTHLY  — e.g. price_1...
  STRIPE_PRICE_PACK_200       — e.g. price_1...
  STRIPE_PRICE_PACK_600       — e.g. price_1...
  STRIPE_PRICE_PACK_1500      — e.g. price_1...

  STRIPE_CUSTOMER_PORTAL_URL  — optional, set if you configure a hosted portal URL
"""
import os
from datetime import datetime, timedelta, timezone

import stripe
from flask import (
    Blueprint,
    current_app,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from sqlalchemy import func

from auth import login_required
from billing_service import (
    apply_credit_pack_purchase,
    apply_plan_purchase,
    get_billing_summary,
    list_credit_packs,
    list_plan_cards,
    normalize_plan_tier,
    refresh_cycle_if_needed,
)
from db import ApiRequestEvent, UsageEvent, User, db

billing_bp = Blueprint("billing", __name__)

# ── Stripe helpers ────────────────────────────────────────────────────────────

_PLAN_PRICE_ENV = {
    "pro": "STRIPE_PRICE_PRO_MONTHLY",
    "ultra": "STRIPE_PRICE_ULTRA_MONTHLY",
}

_PACK_PRICE_ENV = {
    "pack_200": "STRIPE_PRICE_PACK_200",
    "pack_600": "STRIPE_PRICE_PACK_600",
    "pack_1500": "STRIPE_PRICE_PACK_1500",
}


def _stripe_enabled() -> bool:
    return bool((os.getenv("STRIPE_SECRET_KEY") or "").strip())


def _stripe_client() -> stripe.StripeClient:
    key = (os.getenv("STRIPE_SECRET_KEY") or "").strip()
    if not key:
        raise RuntimeError("STRIPE_SECRET_KEY is not configured.")
    return stripe.StripeClient(key)


def _get_or_create_stripe_customer(client: stripe.StripeClient, user: User) -> str:
    """Return existing Stripe customer ID or create a new one."""
    if user.stripe_customer_id:
        return user.stripe_customer_id
    customer = client.customers.create(params={
        "email": user.email,
        "name": user.username,
        "metadata": {"user_id": str(user.id)},
    })
    user.stripe_customer_id = customer.id
    db.session.flush()
    return customer.id


def _plan_price_id(plan_tier: str) -> str | None:
    env_key = _PLAN_PRICE_ENV.get(plan_tier)
    if not env_key:
        return None
    return (os.getenv(env_key) or "").strip() or None


def _pack_price_id(pack_id: str) -> str | None:
    env_key = _PACK_PRICE_ENV.get(pack_id)
    if not env_key:
        return None
    return (os.getenv(env_key) or "").strip() or None


# ── Internal helper ───────────────────────────────────────────────────────────

def _get_user():
    user = db.session.get(User, session["user_id"])
    if refresh_cycle_if_needed(user):
        db.session.commit()
    return user


# ── Pages ─────────────────────────────────────────────────────────────────────

@billing_bp.route("/upgrade")
@login_required
def upgrade_page():
    user = _get_user()
    summary = get_billing_summary(user)
    plans = list_plan_cards(summary["plan_tier"])
    return render_template(
        "upgrade.html",
        billing_summary=summary,
        plans=plans,
        feature=request.args.get("feature", ""),
        stripe_enabled=_stripe_enabled(),
        stripe_publishable_key=(os.getenv("STRIPE_PUBLISHABLE_KEY") or "").strip(),
    )


@billing_bp.route("/credits-store")
@login_required
def credits_store_page():
    user = _get_user()
    summary = get_billing_summary(user)
    if normalize_plan_tier(summary["plan_tier"]) == "free":
        return redirect(url_for("billing.upgrade_page"))

    packs = list_credit_packs()
    return render_template(
        "credits_store.html",
        billing_summary=summary,
        packs=packs,
        stripe_enabled=_stripe_enabled(),
        stripe_publishable_key=(os.getenv("STRIPE_PUBLISHABLE_KEY") or "").strip(),
    )


# ── Stripe Checkout ───────────────────────────────────────────────────────────

@billing_bp.route("/billing/stripe/checkout/plan", methods=["POST"])
@login_required
def stripe_checkout_plan():
    if not _stripe_enabled():
        return jsonify({"error": "Payment processing is not configured."}), 503

    user = _get_user()
    data = request.get_json() or {}
    plan_tier = (data.get("plan") or "").strip().lower()
    plan_tier = normalize_plan_tier(plan_tier)

    if plan_tier == "free":
        return jsonify({"error": "Invalid plan."}), 400

    price_id = _plan_price_id(plan_tier)
    if not price_id:
        return jsonify({"error": f"Stripe Price ID for plan '{plan_tier}' is not configured."}), 503

    try:
        client = _stripe_client()
        customer_id = _get_or_create_stripe_customer(client, user)
        db.session.commit()

        success_url = url_for("billing.checkout_success", _external=True) + "?session_id={CHECKOUT_SESSION_ID}"
        cancel_url = url_for("billing.upgrade_page", _external=True)

        checkout_session = client.checkout.sessions.create(params={
            "mode": "subscription",
            "customer": customer_id,
            "line_items": [{"price": price_id, "quantity": 1}],
            "success_url": success_url,
            "cancel_url": cancel_url,
            "metadata": {
                "user_id": str(user.id),
                "plan_tier": plan_tier,
            },
            "subscription_data": {
                "metadata": {
                    "user_id": str(user.id),
                    "plan_tier": plan_tier,
                }
            },
            "allow_promotion_codes": True,
            "tax_id_collection": {"enabled": True},
            "automatic_tax": {"enabled": True},
        })
        return jsonify({"checkout_url": checkout_session.url})
    except stripe.StripeError as e:
        return jsonify({"error": str(e.user_message or e)}), 400


@billing_bp.route("/billing/stripe/checkout/credits", methods=["POST"])
@login_required
def stripe_checkout_credits():
    if not _stripe_enabled():
        return jsonify({"error": "Payment processing is not configured."}), 503

    user = _get_user()
    if normalize_plan_tier(user.plan_tier) == "free":
        return jsonify({
            "error": "Credit packs require a paid plan.",
            "reason": "plan_required",
            "redirect_url": "/upgrade",
        }), 403

    data = request.get_json() or {}
    pack_id = (data.get("pack_id") or "").strip()
    price_id = _pack_price_id(pack_id)
    if not price_id:
        return jsonify({"error": "Invalid credit pack or Stripe Price ID not configured."}), 400

    try:
        client = _stripe_client()
        customer_id = _get_or_create_stripe_customer(client, user)
        db.session.commit()

        success_url = url_for("billing.checkout_success", _external=True) + "?session_id={CHECKOUT_SESSION_ID}"
        cancel_url = url_for("billing.credits_store_page", _external=True)

        checkout_session = client.checkout.sessions.create(params={
            "mode": "payment",
            "customer": customer_id,
            "line_items": [{"price": price_id, "quantity": 1}],
            "success_url": success_url,
            "cancel_url": cancel_url,
            "metadata": {
                "user_id": str(user.id),
                "pack_id": pack_id,
                "payment_type": "credits",
            },
            "allow_promotion_codes": True,
            "tax_id_collection": {"enabled": True},
            "automatic_tax": {"enabled": True},
        })
        return jsonify({"checkout_url": checkout_session.url})
    except stripe.StripeError as e:
        return jsonify({"error": str(e.user_message or e)}), 400


@billing_bp.route("/billing/checkout/success")
@login_required
def checkout_success():
    return render_template("checkout_success.html")


@billing_bp.route("/billing/stripe/portal", methods=["POST"])
@login_required
def stripe_customer_portal():
    if not _stripe_enabled():
        return jsonify({"error": "Billing portal is not configured."}), 503

    user = _get_user()
    if not user.stripe_customer_id:
        return jsonify({"error": "No billing account found. Please make a purchase first."}), 400

    try:
        client = _stripe_client()
        return_url = url_for("main.dashboard", _external=True)
        portal_session = client.billing_portal.sessions.create(params={
            "customer": user.stripe_customer_id,
            "return_url": return_url,
        })
        return jsonify({"portal_url": portal_session.url})
    except stripe.StripeError as e:
        return jsonify({"error": str(e.user_message or e)}), 400


# ── Stripe Webhook ────────────────────────────────────────────────────────────

@billing_bp.route("/billing/stripe/webhook", methods=["POST"])
def stripe_webhook():
    webhook_secret = (os.getenv("STRIPE_WEBHOOK_SECRET") or "").strip()
    if not webhook_secret:
        current_app.logger.error("STRIPE_WEBHOOK_SECRET is not configured.")
        return "", 500

    payload = request.get_data(as_text=False)
    sig_header = request.headers.get("Stripe-Signature", "")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
    except (stripe.SignatureVerificationError, ValueError) as e:
        current_app.logger.warning(f"Stripe webhook signature verification failed: {e}")
        return "", 400

    try:
        _handle_stripe_event(event)
    except Exception as e:
        current_app.logger.error(
            f"Stripe webhook handler error for {event['type']}: {e}", exc_info=True
        )
        return "", 500

    return "", 200


def _handle_stripe_event(event) -> None:
    event_type = event["type"]
    data_object = event["data"]["object"]

    if event_type == "checkout.session.completed":
        _handle_checkout_completed(data_object)

    elif event_type in ("customer.subscription.updated", "customer.subscription.created"):
        _handle_subscription_updated(data_object)

    elif event_type == "customer.subscription.deleted":
        _handle_subscription_deleted(data_object)

    elif event_type == "invoice.payment_failed":
        _handle_invoice_payment_failed(data_object)


def _user_by_stripe_customer(customer_id: str) -> User | None:
    return User.query.filter_by(stripe_customer_id=customer_id).first()


def _handle_checkout_completed(session_obj) -> None:
    meta = session_obj.get("metadata") or {}
    user_id = meta.get("user_id")
    payment_type = meta.get("payment_type", "")
    plan_tier = meta.get("plan_tier", "")
    pack_id = meta.get("pack_id", "")
    mode = session_obj.get("mode", "")

    if not user_id:
        return

    user = db.session.get(User, int(user_id))
    if not user:
        return

    # Store/update stripe_customer_id from the completed session
    customer_id = session_obj.get("customer")
    if customer_id and not user.stripe_customer_id:
        user.stripe_customer_id = customer_id

    if mode == "subscription" and plan_tier:
        subscription_id = session_obj.get("subscription")
        if subscription_id:
            user.stripe_subscription_id = subscription_id
            user.stripe_subscription_status = "active"
        success, _ = apply_plan_purchase(user.id, plan_tier)
        if success:
            db.session.commit()

    elif mode == "payment" and pack_id:
        success, _ = apply_credit_pack_purchase(user.id, pack_id)
        if success:
            db.session.commit()


def _handle_subscription_updated(subscription) -> None:
    customer_id = subscription.get("customer")
    status = subscription.get("status", "")
    subscription_id = subscription.get("id", "")
    meta = (subscription.get("metadata") or {})
    plan_tier = meta.get("plan_tier", "")

    user = _user_by_stripe_customer(customer_id)
    if not user:
        return

    user.stripe_subscription_id = subscription_id
    user.stripe_subscription_status = status

    if status == "active" and plan_tier:
        # Re-apply plan in case it changed
        apply_plan_purchase(user.id, plan_tier)

    db.session.commit()


def _handle_subscription_deleted(subscription) -> None:
    customer_id = subscription.get("customer")
    user = _user_by_stripe_customer(customer_id)
    if not user:
        return

    user.stripe_subscription_id = None
    user.stripe_subscription_status = "canceled"
    user.plan_tier = "free"
    db.session.commit()


def _handle_invoice_payment_failed(invoice) -> None:
    customer_id = invoice.get("customer")
    user = _user_by_stripe_customer(customer_id)
    if not user:
        return

    user.stripe_subscription_status = "past_due"
    db.session.commit()


# ── Account / usage endpoints ─────────────────────────────────────────────────

@billing_bp.route("/billing/account")
@login_required
def billing_account():
    user = _get_user()
    summary = get_billing_summary(user)
    summary["stripe_enabled"] = _stripe_enabled()
    summary["has_stripe_customer"] = bool(user.stripe_customer_id)
    summary["stripe_subscription_status"] = user.stripe_subscription_status
    return jsonify(summary)


@billing_bp.route("/billing/usage")
@login_required
def billing_usage():
    user = _get_user()
    try:
        days = int(request.args.get("days", 30))
    except Exception:
        days = 30
    days = max(1, min(days, 365))
    since = datetime.now(timezone.utc) - timedelta(days=days)

    base_query = ApiRequestEvent.query.filter(
        ApiRequestEvent.user_id == user.id,
        ApiRequestEvent.created_at >= since,
    )

    totals = base_query.with_entities(
        func.count(ApiRequestEvent.id),
        func.coalesce(func.sum(ApiRequestEvent.estimated_cost_usd), 0),
        func.coalesce(func.sum(ApiRequestEvent.estimated_cost_eur), 0),
        func.coalesce(func.sum(ApiRequestEvent.total_tokens), 0),
        func.coalesce(func.sum(ApiRequestEvent.input_tokens), 0),
        func.coalesce(func.sum(ApiRequestEvent.output_tokens), 0),
    ).first()

    feature_rows = base_query.with_entities(
        ApiRequestEvent.feature,
        func.count(ApiRequestEvent.id),
        func.coalesce(func.sum(ApiRequestEvent.estimated_cost_usd), 0),
        func.coalesce(func.sum(ApiRequestEvent.estimated_cost_eur), 0),
        func.coalesce(func.sum(ApiRequestEvent.total_tokens), 0),
    ).group_by(ApiRequestEvent.feature).order_by(
        func.coalesce(func.sum(ApiRequestEvent.estimated_cost_eur), 0).desc()
    ).all()

    model_rows = base_query.with_entities(
        ApiRequestEvent.model,
        func.count(ApiRequestEvent.id),
        func.coalesce(func.sum(ApiRequestEvent.estimated_cost_usd), 0),
        func.coalesce(func.sum(ApiRequestEvent.estimated_cost_eur), 0),
        func.coalesce(func.sum(ApiRequestEvent.total_tokens), 0),
    ).group_by(ApiRequestEvent.model).order_by(
        func.coalesce(func.sum(ApiRequestEvent.estimated_cost_eur), 0).desc()
    ).limit(20).all()

    credit_rows = UsageEvent.query.filter(
        UsageEvent.user_id == user.id,
        UsageEvent.created_at >= since,
    ).with_entities(
        UsageEvent.feature,
        func.count(UsageEvent.id),
        func.coalesce(func.sum(UsageEvent.credits_charged), 0),
        func.coalesce(func.sum(UsageEvent.estimated_cost_usd), 0),
    ).group_by(UsageEvent.feature).order_by(
        func.coalesce(func.sum(UsageEvent.credits_charged), 0).desc()
    ).all()

    return jsonify({
        "period_days": days,
        "since": since.isoformat(),
        "totals": {
            "request_count": int(totals[0] or 0),
            "estimated_cost_usd": str(totals[1] or 0),
            "estimated_cost_eur": str(totals[2] or 0),
            "total_tokens": int(totals[3] or 0),
            "input_tokens": int(totals[4] or 0),
            "output_tokens": int(totals[5] or 0),
        },
        "by_feature": [
            {
                "feature": row[0] or "unknown",
                "request_count": int(row[1] or 0),
                "estimated_cost_usd": str(row[2] or 0),
                "estimated_cost_eur": str(row[3] or 0),
                "total_tokens": int(row[4] or 0),
            }
            for row in feature_rows
        ],
        "by_model": [
            {
                "model": row[0] or "unknown",
                "request_count": int(row[1] or 0),
                "estimated_cost_usd": str(row[2] or 0),
                "estimated_cost_eur": str(row[3] or 0),
                "total_tokens": int(row[4] or 0),
            }
            for row in model_rows
        ],
        "credits_by_feature": [
            {
                "feature": row[0] or "unknown",
                "events": int(row[1] or 0),
                "credits_charged": int(row[2] or 0),
                "estimated_usage_cost_usd": str(row[3] or 0),
            }
            for row in credit_rows
        ],
    })
