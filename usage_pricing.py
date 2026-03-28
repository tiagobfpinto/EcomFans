from __future__ import annotations

import os
from decimal import Decimal, ROUND_HALF_UP


MILLION = Decimal("1000000")
USD_TO_EUR_RATE = Decimal(os.getenv("USD_TO_EUR_RATE", "0.92"))

# Gemini text token pricing (USD per 1M tokens).
GEMINI_TOKEN_PRICING = {
    "gemini-2.5-flash-lite": {
        "input_per_million": Decimal("0.10"),
        "output_per_million": Decimal("0.40"),
    },
    "gemini-2.5-flash": {
        "input_per_million": Decimal("0.30"),
        "output_per_million": Decimal("2.50"),
    },
}

# Gemini image pricing (USD per image) for known production models.
GEMINI_IMAGE_PRICING = {
    "gemini-2.5-flash-image": {
        "standard": Decimal("0.039"),
        "batch": Decimal("0.0195"),
    },
}


def normalize_model_name(model: str | None) -> str | None:
    if not model:
        return None
    model_name = model.strip()
    if model_name.startswith("models/"):
        return model_name.split("/", 1)[1]
    return model_name


def _to_decimal_tokens(value: int | None) -> Decimal:
    if value is None:
        return Decimal("0")
    return Decimal(int(value))


def estimate_gemini_cost_usd(
    model: str | None,
    traffic_type: str = "standard",
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    images_generated: int | None = None,
) -> Decimal | None:
    normalized = normalize_model_name(model)
    if not normalized:
        return None

    traffic = "batch" if traffic_type == "batch" else "standard"

    image_price = GEMINI_IMAGE_PRICING.get(normalized)
    if image_price:
        image_count = max(0, int(images_generated or 0))
        if image_count == 0:
            return Decimal("0")
        return image_price.get(traffic, image_price["standard"]) * Decimal(image_count)

    token_price = GEMINI_TOKEN_PRICING.get(normalized)
    if not token_price:
        return None

    in_tokens = _to_decimal_tokens(input_tokens)
    out_tokens = _to_decimal_tokens(output_tokens)
    input_cost = (in_tokens / MILLION) * token_price["input_per_million"]
    output_cost = (out_tokens / MILLION) * token_price["output_per_million"]
    return input_cost + output_cost


def usd_to_eur(cost_usd: Decimal | None) -> Decimal | None:
    if cost_usd is None:
        return None
    return (cost_usd * USD_TO_EUR_RATE).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
