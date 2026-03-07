from flask import Blueprint, jsonify, redirect, render_template, request, session, url_for

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
from db import User, db

billing_bp = Blueprint("billing", __name__)


def _get_user():
    user = db.session.get(User, session["user_id"])
    if refresh_cycle_if_needed(user):
        db.session.commit()
    return user


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
    )


@billing_bp.route("/billing/mock/checkout/plan", methods=["POST"])
@login_required
def mock_checkout_plan():
    user = _get_user()
    data = request.get_json() or {}
    plan = (data.get("plan") or "").strip().lower()

    success, payload = apply_plan_purchase(user.id, plan)
    if not success:
        return jsonify(payload or {"error": "Plan checkout failed."}), 400

    db.session.commit()

    refreshed = db.session.get(User, user.id)
    summary = get_billing_summary(refreshed)
    return jsonify({"success": True, "billing": summary, "purchase": payload})


@billing_bp.route("/billing/mock/checkout/credits", methods=["POST"])
@login_required
def mock_checkout_credits():
    user = _get_user()
    if normalize_plan_tier(user.plan_tier) == "free":
        return jsonify(
            {
                "error": "Credit packs require a paid plan.",
                "reason": "plan_required",
                "required_plan": "pro",
                "redirect_url": "/upgrade",
            }
        ), 403

    data = request.get_json() or {}
    pack_id = (data.get("pack_id") or "").strip()

    success, payload = apply_credit_pack_purchase(user.id, pack_id)
    if not success:
        return jsonify(payload or {"error": "Credit checkout failed."}), 400

    db.session.commit()

    refreshed = db.session.get(User, user.id)
    summary = get_billing_summary(refreshed)
    return jsonify({"success": True, "billing": summary, "purchase": payload})


@billing_bp.route("/billing/account")
@login_required
def billing_account():
    user = _get_user()
    summary = get_billing_summary(user)
    return jsonify(summary)
