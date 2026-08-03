"""OPTIONAL LLM step-wording refinement. Disabled unless
VIDREPRO_LLM_PROVIDER=anthropic. The model may only rewrite step *text* for
readability; structure, order, evidence, confidence, and every other field are
preserved verbatim — a hallucinated or missing step is simply discarded."""
import json

import requests

from vidrepro.config import get_settings
from vidrepro.contracts.report import ReportBody

SYSTEM = (
    "You rewrite bug-reproduction step sentences for clarity. You receive a JSON "
    "array of {key, text}. Return ONLY a JSON array of {key, text} with the same "
    "keys. Do not add, remove, merge, or reorder steps. Do not add information "
    "that is not in the original text. Keep quoted UI text verbatim."
)


def refine_step_texts(body: ReportBody) -> ReportBody:
    s = get_settings()
    steps_payload = [{"key": st.key, "text": st.text} for st in body.steps]
    if not steps_payload:
        return body
    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": s.anthropic_api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": s.llm_model,
            "max_tokens": 2000,
            "system": SYSTEM,
            "messages": [{"role": "user", "content": json.dumps(steps_payload)}],
        },
        timeout=60,
    )
    resp.raise_for_status()
    text = resp.json()["content"][0]["text"].strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    refined = {item["key"]: item["text"] for item in json.loads(text)}
    for step in body.steps:
        new_text = refined.get(step.key, "").strip()
        if new_text:  # unknown/missing keys are ignored — structure is inviolable
            step.text = new_text
    return body
