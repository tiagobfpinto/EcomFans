Yes. If you’re generating only a small number of final images and still paying a lot, the bill is probably not mainly the final renders. On Gemini right now, Gemini 2.5 Flash Image is $0.039 per image at standard pricing and $0.0195 per image in Batch, while Gemini 3.1 Flash Image Preview is $0.067 per 1K image. So even 100 final images is only about $3.90 on 2.5 Flash Image standard, $1.95 in batch, or $6.70 on 3.1 Flash Image at 1K. If your spend is much higher than that, the expensive part is usually the analysis / prompting / retries / repeated media uploads, not the last image call.

The biggest fix is to split your pipeline by cost:

use a cheap text/vision model for planning, extraction, scoring, JSON generation, and QA

use the image model only for the final render

A good default is:

Gemini 2.5 Flash-Lite for prompt planning / ad analysis / scoring

Gemini 2.5 Flash Image for the final image

Batch API for anything non-instant

On the current pricing page, 2.5 Flash-Lite is $0.10 input / $0.40 output per 1M tokens, while 2.5 Flash is $0.30 input / $2.50 output. That means 2.5 Flash output is about 6.25x pricier than 2.5 Flash-Lite for text work, so using Flash for every planning step burns money fast.

So for your SaaS, I’d do this:

Do not generate images until the user approves a cheap draft plan.
First run a cheap call that outputs JSON only: hook, scene type, layout, persona, lighting, crop, CTA zone, claim style. Then let the user pick 1 direction. Only after that call the image model. This turns “3–6 expensive tries” into “1 expensive try + cheap planning.” The Gemini docs also explicitly note that Gemini 3 works best with direct, concise prompts, so shorter prompts help both quality and token cost.

Prefer Gemini 2.5 Flash Image over 3.1 Flash Image unless you’ve proved 3.1 lifts conversion enough to justify it.
2.5 Flash Image is cheaper on the official pricing page: $0.039/image standard and $0.0195/image batch. If your product is about volume testing, that pricing matters more than marginal quality gains.

Use Batch API for queued jobs.
Google’s Batch API is designed for non-urgent jobs at 50% of standard cost. That is perfect for back-office generations, overnight creative packs, regenerations, and competitor-ad analysis queues.

Stop re-uploading the same product and inspiration images every time.
Use the Files API for reused media. Google says Files API storage is available at no cost, files are stored for 48 hours, and they can be reused across requests. If users keep iterating on the same product pack or inspiration ad, this alone can cut waste.

Use caching for repeated prompt prefixes and shared context.
Gemini has implicit caching enabled by default on supported models, and Google says explicit caching can lower cost when you reuse the same corpus across requests. If your system prompt, brand rules, or repeated product description stays similar, keep that stable and at the start of the prompt so cache hits are more likely. You can verify hits via usage_metadata.cached_content_token_count.

Lower media resolution for cheap steps.
For Gemini 2.5 models, image token usage can be as low as 64 tokens at LOW and 256 at MEDIUM, while higher settings or pan-and-scan cost more. For Gemini 3 models, HIGH image resolution is around 1120 tokens and LOW around 280. So do LOW/MEDIUM for “is this a supplement bottle ad?” or “extract layout from this creative,” and only use HIGH when you truly need fine visual detail.

Instrument every call.
