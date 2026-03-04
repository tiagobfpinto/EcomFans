import json

import anthropic


EXTRACTION_PROMPT = """Analyze this YouTube video transcript and extract all product mentions.

For each product mentioned, provide:
- product_name: The product name as mentioned
- normalized_name: A lowercase, standardized version (e.g., "dyson airwrap" not "Dyson Air Wrap")
- brand: The brand/company name if identifiable
- category: Product category (e.g., "Skincare", "Haircare", "Electronics", "Fashion", "Food", "Fitness", "Home", "Beauty", "Tech", "Other")
- sentiment: "positive", "negative", or "neutral"
- context_quote: A brief excerpt showing how the product was mentioned (max 150 chars)
- mention_type: "review", "recommendation", "comparison", "casual_mention", or "tutorial"
- is_sponsored: true if the mention appears to be sponsored/paid, false otherwise

Return ONLY valid JSON in this exact format:
{
  "products": [
    {
      "product_name": "...",
      "normalized_name": "...",
      "brand": "...",
      "category": "...",
      "sentiment": "...",
      "context_quote": "...",
      "mention_type": "...",
      "is_sponsored": false
    }
  ],
  "video_themes": ["theme1", "theme2"]
}

If no products are found, return {"products": [], "video_themes": []}.

Transcript:
"""

MAX_TRANSCRIPT_CHARS = 80000


def analyze_transcript(transcript_text, api_key):
    """Send transcript to Claude for product extraction."""
    try:
        truncated = transcript_text[:MAX_TRANSCRIPT_CHARS]
        client = anthropic.Anthropic(api_key=api_key)

        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4096,
            messages=[
                {
                    "role": "user",
                    "content": EXTRACTION_PROMPT + truncated,
                }
            ],
        )

        response_text = message.content[0].text

        # Extract JSON from response (handle markdown code blocks)
        json_text = response_text
        if "```" in json_text:
            json_match = json_text.split("```json")[-1].split("```")[0] if "```json" in json_text else json_text.split("```")[1].split("```")[0]
            json_text = json_match.strip()

        data = json.loads(json_text)
        return {"data": data, "success": True, "error": None}

    except json.JSONDecodeError as e:
        return {"data": None, "success": False, "error": f"Failed to parse AI response: {e}"}
    except Exception as e:
        return {"data": None, "success": False, "error": str(e)}
