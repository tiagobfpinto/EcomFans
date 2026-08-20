from decimal import Decimal

PLAN_ORDER = ["free", "pro", "ultra"]

PLAN_DEFINITIONS = {
    "free": {
        "name": "Free",
        "monthly_price_eur": Decimal("0"),
        "monthly_credits": 30,
        "avatars_enabled": False,
        "social_downloader_enabled": False,
    },
    "pro": {
        "name": "Pro",
        "monthly_price_eur": Decimal("39"),
        "monthly_credits": 600,
        "avatars_enabled": True,
        "social_downloader_enabled": True,
    },
    "ultra": {
        "name": "Ultra",
        "monthly_price_eur": Decimal("99"),
        "monthly_credits": 2000,
        "avatars_enabled": True,
        "social_downloader_enabled": True,
    },
}

CREDIT_PACKS = {
    "pack_200": {
        "id": "pack_200",
        "name": "200 Credits",
        "credits": 200,
        "price_eur": Decimal("19"),
    },
    "pack_600": {
        "id": "pack_600",
        "name": "600 Credits",
        "credits": 600,
        "price_eur": Decimal("49"),
    },
    "pack_1500": {
        "id": "pack_1500",
        "name": "1500 Credits",
        "credits": 1500,
        "price_eur": Decimal("99"),
    },
}

# Credits charged by feature/use.
CREDIT_COSTS = {
    "ai_image_variation": 2,
    "ai_creatives_single_provider_inspiration": 2,
    "ai_creatives_both_provider_inspiration": 4,
    "ai_creatives_prompt_only_inspiration": 1,
    "avatars_pair": 4,
}

# Approximate infra/API cost in USD per completed unit.
ESTIMATED_USAGE_COST_USD = {
    "ai_image_variation": Decimal("0.040"),
    "ai_creatives_gemini_inspiration": Decimal("0.041"),
    "ai_creatives_openai_inspiration": Decimal("0.043"),
    "ai_creatives_both_inspiration": Decimal("0.083"),
    "ai_creatives_prompt_only_inspiration": Decimal("0.0015"),
    "avatars_pair": Decimal("0.079"),
}

BILLING_CYCLE_DAYS = 30
CURRENCY = "EUR"

ERROR_REASON_UPGRADE_REQUIRED = "upgrade_required"
ERROR_REASON_BUY_CREDITS_REQUIRED = "buy_credits_required"
ERROR_REASON_PLAN_REQUIRED = "plan_required"
