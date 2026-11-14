"""Recognize a product from a picture of its pack, against the wholesaler's catalog.

The Valio product data has no pictures, so a picture cannot be compared with a
picture. It has two things that are printed on every pack: the number under the
barcode (GTIN) and the product name. A vision model reads those from the
picture. Plain code then finds the product: by the number if the model read
one that the catalog knows, else by the words of the name.

The model only reads. It never decides which product it is, and a person
confirms the match before anything is recorded.
"""
from __future__ import annotations

import re
from datetime import date

from .buyer_agent import _setting, first_json, verda_chat

PROMPT = """This is a picture of a food pack in a professional kitchen. Read what is printed on it.
Answer with one JSON object and nothing else:
{"brand": text or null, "name": text or null, "size": text or null, "barcode": digits or null, "best_before": "YYYY-MM-DD" or null}
"barcode" is the number printed under the barcode. "size" is the amount, for example "1 l" or "500 g".
Copy only what you can read. Use null for anything that is not readable. Do not guess."""

# Words that say nothing about which product it is.
COMMON = {"the", "and", "with", "of", "g", "kg", "l", "ml", "dl", "cl", "pcs", "kpl", "x"}
MIN_SCORE = 0.5  # the share of the words on the pack that the catalog name must hold


def ask_model(image_b64: str) -> str:
    """One call to the vision model on Verda. The tests replace this function."""
    key = _setting("VERDA_INFERENCE_API_KEY")
    if not key:
        raise RuntimeError("VERDA_INFERENCE_API_KEY is not set")
    return verda_chat([{"role": "user", "content": [
        {"type": "text", "text": PROMPT},
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + image_b64}}]}], key)


def read_pack(image_b64: str) -> dict:
    """What the model read on the pack, with every field checked. A field that fails its check is None."""
    got = first_json(ask_model(image_b64)) or {}
    text = lambda k: (str(got[k]).strip()[:80] or None) if isinstance(got.get(k), (str, int, float)) else None  # noqa: E731
    digits = re.sub(r"\D", "", str(got.get("barcode") or ""))
    try:
        best_before = date.fromisoformat(str(got.get("best_before"))).isoformat()
    except ValueError:
        best_before = None
    return {"brand": text("brand"), "name": text("name"), "size": text("size"),
            "barcode": digits if 8 <= len(digits) <= 14 else None, "best_before": best_before}


def words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-zåäö]+|\d+(?:[.,]\d+)?", text.lower()) if w not in COMMON}


def match(read: dict, products: list[dict], limit: int = 3) -> tuple[str, list[tuple[dict, float]]]:
    """How the product was found ("barcode", "name" or "none") and the candidates with a score from 0 to 1."""
    code = (read.get("barcode") or "").lstrip("0")
    if code:
        same = [p for p in products if str(p.get("gtin") or "").lstrip("0") == code]
        if same:
            return "barcode", [(p, 1.0) for p in same[:limit]]
    seen = words(" ".join(str(read.get(k) or "") for k in ("brand", "name", "size")))
    if len(seen) < 2:
        return "none", []
    scored = []
    for p in products:
        have = words(f"{p.get('supplier') or ''} {p.get('name') or ''}")
        score = len(seen & have) / len(seen)
        if score >= MIN_SCORE:
            scored.append((p, round(score, 2), len(have)))
    scored.sort(key=lambda x: (-x[1], x[2], str(x[0].get("id"))))  # the best score first, then the shortest name
    return ("name", [(p, s) for p, s, _ in scored[:limit]]) if scored else ("none", [])
