from flask import Blueprint, jsonify, render_template, request, session

from ai_service import gemini_vision_with_meta, openai_chat_with_meta
from auth import login_required
from billing_service import get_effective_api_key
from db import User, db

landing_builder_bp = Blueprint("landing_builder", __name__)

OPENAI_TEXT_MODELS = ["gpt-4o", "gpt-4o-mini"]
GEMINI_TEXT_MODELS = ["gemini-2.5-flash-lite", "gemini-2.5-flash", "gemini-2.5-pro"]
VALID_PROVIDERS = {"openai", "gemini"}

MAX_PROMPT_CHARS = 8000
MAX_CONTEXT_CHARS = 8000
MAX_INCOMING_CHARS = 12000

_RESEARCHER_SYSTEM = "You are a helpful research assistant for e-commerce and marketing."
_DEFAULT_SYSTEM = "You are a helpful AI assistant for e-commerce and marketing."


def _get_user():
    return db.session.get(User, session["user_id"])


@landing_builder_bp.route("/landing-builder")
@login_required
def landing_builder_page():
    user = _get_user()
    gemini_key = get_effective_api_key(user.id, "gemini")
    openai_key = get_effective_api_key(user.id, "openai")
    return render_template(
        "landing_builder.html",
        has_gemini_key=bool(gemini_key),
        has_openai_key=bool(openai_key),
    )


def _call_ai(api_key, system_prompt, user_message, provider, model):
    if provider == "openai":
        return openai_chat_with_meta(
            api_key=api_key,
            system_prompt=system_prompt,
            user_content=user_message,
            model=model,
            max_tokens=4096,
            temperature=0.7,
        )
    return gemini_vision_with_meta(
        api_key=api_key,
        system_prompt=system_prompt,
        parts=[{"text": user_message}],
        max_tokens=4096,
        temperature=0.7,
        model=model,
    )


@landing_builder_bp.route("/landing-builder/run-node", methods=["POST"])
@login_required
def run_node():
    user = _get_user()
    data = request.get_json() or {}

    node_type     = (data.get("type") or "researcher").strip()
    prompt        = (data.get("prompt") or "").strip()
    context       = (data.get("context") or "").strip()
    system_prompt = (data.get("system_prompt") or "").strip()
    incoming_text = (data.get("incoming_text") or "").strip()
    provider      = (data.get("provider") or "openai").strip()
    model         = (data.get("model") or "").strip()

    if not prompt:
        return jsonify({"error": "Prompt is required."}), 400
    if provider not in VALID_PROVIDERS:
        return jsonify({"error": "Invalid provider."}), 400
    if len(prompt) > MAX_PROMPT_CHARS:
        return jsonify({"error": f"Prompt must be <= {MAX_PROMPT_CHARS} characters."}), 400
    if len(context) > MAX_CONTEXT_CHARS:
        return jsonify({"error": f"Context must be <= {MAX_CONTEXT_CHARS} characters."}), 400

    if provider == "openai":
        if model not in OPENAI_TEXT_MODELS:
            model = OPENAI_TEXT_MODELS[0]
    else:
        if model not in GEMINI_TEXT_MODELS:
            model = GEMINI_TEXT_MODELS[0]

    api_key = get_effective_api_key(user.id, provider)
    if not api_key:
        return jsonify({"error": f"no_{provider}_key"}), 400

    # System prompt selection
    if node_type == "researcher":
        effective_system = _RESEARCHER_SYSTEM
    else:
        effective_system = system_prompt.strip() or _DEFAULT_SYSTEM

    # Assemble user message: incoming → context → prompt
    parts = []
    if incoming_text:
        truncated = incoming_text[:MAX_INCOMING_CHARS]
        if len(incoming_text) > MAX_INCOMING_CHARS:
            truncated += "\n[…truncated]"
        parts.append(f"[INCOMING DATA]\n{truncated}")
    if context:
        parts.append(f"[CONTEXT]\n{context}")
    parts.append(prompt)
    user_message = "\n---\n".join(parts)

    try:
        result_text, meta = _call_ai(api_key, effective_system, user_message, provider, model)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    return jsonify({
        "result": result_text,
        "provider": provider,
        "model": meta.get("model") or model,
    })


# Legacy endpoint — kept for backward compat, delegates to run_node logic
@landing_builder_bp.route("/landing-builder/run-researcher", methods=["POST"])
@login_required
def run_researcher():
    user = _get_user()
    data = request.get_json() or {}

    prompt   = (data.get("prompt") or "").strip()
    context  = (data.get("context") or "").strip()
    provider = (data.get("provider") or "openai").strip()
    model    = (data.get("model") or "").strip()

    if not prompt:
        return jsonify({"error": "Prompt is required."}), 400
    if provider not in VALID_PROVIDERS:
        return jsonify({"error": "Invalid provider."}), 400
    if len(prompt) > MAX_PROMPT_CHARS:
        return jsonify({"error": f"Prompt must be <= {MAX_PROMPT_CHARS} characters."}), 400
    if len(context) > MAX_CONTEXT_CHARS:
        return jsonify({"error": f"Context must be <= {MAX_CONTEXT_CHARS} characters."}), 400

    if provider == "openai":
        if model not in OPENAI_TEXT_MODELS:
            model = OPENAI_TEXT_MODELS[0]
    else:
        if model not in GEMINI_TEXT_MODELS:
            model = GEMINI_TEXT_MODELS[0]

    api_key = get_effective_api_key(user.id, provider)
    if not api_key:
        return jsonify({"error": f"no_{provider}_key"}), 400

    user_message = f"CONTEXT:\n{context}\n\n---\n\n{prompt}" if context else prompt

    try:
        result_text, meta = _call_ai(api_key, _RESEARCHER_SYSTEM, user_message, provider, model)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    return jsonify({
        "result": result_text,
        "provider": provider,
        "model": meta.get("model") or model,
    })
