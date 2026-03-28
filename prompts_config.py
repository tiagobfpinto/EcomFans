"""Prompt constants for AI Creatives generation modes.

Import these by name — do not hard-code prompt text inline.
"""

# ── Competitor Inspiration mode ───────────────────────────────────────

SYSTEM_PROMPT_COMPETITOR_MODE = (
    "You are an expert performance marketing strategist and ad creative director. "
    "You create conversion-optimised ad concepts grounded in customer psychology and "
    "awareness levels. Output only the requested content — no preamble, no commentary, "
    "no markdown code fences."
)

PROMPT_COMPETITOR_ANALYSIS = (
    "Analyse this competitor ad image in depth. Return a structured analysis covering:\n\n"
    "1. FORMAT & PLACEMENT: Ad format (static image, story, banner, etc.), estimated aspect "
    "ratio, likely platform.\n"
    "2. VISUAL COMPOSITION: Layout structure, hero element, colour palette, typography style, "
    "imagery type (lifestyle, product, illustrated, UGC-style).\n"
    "3. COPYWRITING: Headline approach, body copy tone, CTA text and style.\n"
    "4. EMOTIONAL APPEAL: Core emotion targeted (fear, desire, curiosity, social proof, "
    "urgency, etc.).\n"
    "5. OFFER STRUCTURE: What is being offered and how the value is framed (discount, "
    "benefit, transformation, feature).\n"
    "6. AWARENESS LEVEL: Which customer awareness level does this ad target?\n"
    "   - unaware: Prospect doesn't know they have the problem\n"
    "   - problem_aware: Knows the pain but hasn't found a fix\n"
    "   - solution_aware: Knows fixes exist, comparing options\n"
    "   - product_aware: Knows this product, hasn't bought yet\n"
    "   - most_aware: Past buyers or very familiar\n"
    "   State the single best-fit level on its own line exactly as: AWARENESS_LEVEL: <value>\n\n"
    "7. STRATEGIC INSIGHT: What makes this ad effective and what can be adapted for a "
    "different product.\n\n"
    "Be concise and specific."
)

# Placeholders in this prompt: {{PHASE_1_ANALYSIS_OUTPUT}}, {{USER_PRODUCT_INFO}},
# {{AWARENESS_LEVEL}}, {{PLATFORM}}
PROMPT_COMPETITOR_GENERATION = (
    "You are creating a new ad concept for a product, inspired by a competitor's ad strategy.\n\n"
    "## Competitor Ad Analysis\n"
    "{{PHASE_1_ANALYSIS_OUTPUT}}\n\n"
    "## Product Information\n"
    "{{USER_PRODUCT_INFO}}\n\n"
    "## Target Awareness Level: {{AWARENESS_LEVEL}}\n"
    "## Target Platform: {{PLATFORM}}\n\n"
    "Create a complete, original ad concept tailored to the awareness level and platform. "
    "Draw strategic inspiration from the competitor's approach but write entirely original "
    "copy specific to the product above.\n\n"
    "Structure your output with these labelled sections:\n\n"
    "HOOK\n"
    "Opening line or visual hook (1-2 sentences).\n\n"
    "HEADLINE\n"
    "Primary headline text.\n\n"
    "BODY COPY\n"
    "Main ad copy (3-5 sentences).\n\n"
    "CTA\n"
    "Call-to-action text.\n\n"
    "VISUAL DIRECTION\n"
    "Description of the ideal visual and creative direction.\n\n"
    "RATIONALE\n"
    "Brief note on why this approach fits the selected awareness level."
)

# Appended to the LAST variant call when multi_variant=True
PROMPT_MULTI_VARIANT_SUFFIX = (
    "\n\n---\n\n"
    "STRATEGIC RECOMMENDATION\n"
    "You have now generated ad concepts for three awareness levels. "
    "Briefly recommend which awareness level is likely most effective for this product "
    "category and why, and how to split-test these three variants."
)
