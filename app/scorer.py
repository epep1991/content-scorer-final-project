import os
import re
import anthropic
from app.channels import build_system_prompt

REQUIRED_METADATA = ["seo_title", "seo_description", "image_url", "image_alt_text", "tags"]

SCORE_TOOL = {
    "name": "score_component",
    "description": "Score a single content component for paid social readiness.",
    "input_schema": {
        "type": "object",
        "properties": {
            "component_name": {
                "type": "string",
                "enum": ["headline", "short_description", "feature_list", "audience_statement", "cta"],
            },
            "status": {
                "type": "string",
                "enum": ["missing", "embedded", "dependent", "pass"],
            },
            "reason": {
                "type": "string",
                "description": "Required for non-pass statuses. Explain the specific structural issue.",
            },
        },
        "required": ["component_name", "status", "reason"],
    },
}


def strip_html(text):
    if not text:
        return ""
    clean = re.sub(r"<[^>]+>", " ", str(text))
    for entity, char in {"&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&nbsp;": " "}.items():
        clean = clean.replace(entity, char)
    return re.sub(r"\s+", " ", clean).strip()


def check_char_limits(body, channel):
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", body) if s.strip()]
    results = {}
    for component, cfg in channel["char_limits"].items():
        limit = cfg["limit"]
        candidates = [s for s in sentences if len(s) <= limit]
        shortest = min(sentences, key=len) if sentences else ""
        results[component] = {
            "limit": limit,
            "field": cfg["field"],
            "any_candidate_fits": len(candidates) > 0,
            "note": (
                f"{len(candidates)} candidate(s) fit within {limit} chars"
                if candidates
                else f"No candidates fit within {limit} char limit (shortest: {len(shortest)} chars)"
            ),
        }
    return results


def check_metadata(row):
    fields = {f: bool(str(row.get(f, "") or "").strip()) for f in REQUIRED_METADATA}
    return {"fields": fields, "metadata_complete": all(fields.values())}


def score_with_api(product_name, cleaned_content, channel, api_key):
    client = anthropic.Anthropic(api_key=api_key)
    system_prompt = build_system_prompt(channel)
    components = channel["components"]

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        temperature=0.1,
        system=system_prompt,
        tools=[SCORE_TOOL],
        messages=[{
            "role": "user",
            "content": (
                f"Product name: {product_name}\n\nBody content:\n{cleaned_content}\n\n"
                f"Score all five components: {', '.join(components)}. "
                "Call score_component once for each."
            ),
        }],
    )
    return [b.input for b in response.content if b.type == "tool_use" and b.name == "score_component"]


def score_baseline(product_name, cleaned_content, channel, api_key):
    client = anthropic.Anthropic(api_key=api_key)
    platform = channel["platform"]
    placement = channel["placement"]
    system = (
        f"You are a content strategist evaluating product pages for use in an AI-powered "
        f"{platform} {placement} ad generation pipeline. Review the following product page and "
        "identify any issues that would prevent AI from reliably generating ads from this content. "
        "Be specific about what needs to change."
    )
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=512,
        temperature=0.1,
        system=system,
        messages=[{
            "role": "user",
            "content": f"Product name: {product_name}\n\nBody content:\n{cleaned_content}",
        }],
    )
    return response.content[0].text if response.content else ""


def score_row(row, channel, api_key, run_baseline=False):
    product_name = str(row.get("product_name", "Unknown"))
    body_content = str(row.get("body_content", "") or "")
    cleaned = strip_html(body_content)

    char_limits = check_char_limits(cleaned, channel)
    metadata = check_metadata(row)
    model_scores = score_with_api(product_name, cleaned, channel, api_key)

    components = {}
    for s in model_scores:
        name = s["component_name"]
        status = s["status"]
        components[name] = {
            "status": status,
            "reason": s.get("reason") if status != "pass" else None,
            **({"char_limit_note": char_limits[name]["note"]} if name in char_limits else {}),
        }

    passing = sum(1 for c in components.values() if c["status"] == "pass")

    result = {
        "product_name": product_name,
        "passing_count": passing,
        "pipeline_ready": passing == 5 and metadata["metadata_complete"],
        "components": components,
        "metadata": metadata["fields"],
        "metadata_complete": metadata["metadata_complete"],
        "expected_result": "" if str(row.get("expected_result", "")) in ("nan", "None", "") else str(row.get("expected_result", "")),
    }

    if run_baseline:
        result["baseline_feedback"] = score_baseline(product_name, cleaned, channel, api_key)

    return result


def score_all(rows, api_key, channel, run_baseline=False, progress_callback=None):
    results = []
    for i, row in enumerate(rows):
        result = score_row(row, channel, api_key, run_baseline)
        results.append(result)
        if progress_callback:
            progress_callback(i + 1, len(rows), result["product_name"])
    return results


def readiness_tier(passing_count):
    if passing_count == 5:
        return "PASS"
    elif passing_count >= 2:
        return "PARTIAL"
    else:
        return "FAIL"
