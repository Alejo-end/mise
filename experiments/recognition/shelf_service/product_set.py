"""The products the recognition service looks for, read from the shared product-set file.

The active set is the file valio-challenge/products/active.json of the demo branch (schema shelf-product-set/2),
named by SHELF_PRODUCTS; there is no built-in list and no silent fallback to an older set. It gives identities,
labels, flavour families with the text that tells flavours apart, and the routing thresholds of the local model.
The file is re-read when it changes, so an adjusted threshold applies without a restart.

Archived sets (schema shelf-product-set/1, e.g. the three props of 14 Nov) keep their original identities. The
14 Nov regression cases, the synthetic evaluation and the enrolment-based pipeline's history use them unchanged.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_ACTIVE = HERE.parents[2] / "products" / "active.json"  # present when the branch holds the shared file


def norm(text) -> str:
    """Words in lower case with single spaces, padded: 'Mint-Cocoa 250ml' -> ' mint cocoa 250ml '. Letters of any
    language are kept (appelsiinitäysmehu stays one word)."""
    return " " + " ".join(re.findall(r"\w+", str(text or "").lower())) + " "


@dataclass
class ProductSet:
    set_id: str
    products: list[dict]
    families: dict = field(default_factory=dict)
    routing: dict = field(default_factory=dict)
    archived_labels: dict = field(default_factory=dict)
    sha256: str | None = None
    path: str | None = None
    schema: str = "shelf-product-set/2"
    excluded: list = field(default_factory=list)  # removed products: never candidates or counts, known as not inventory
    text_matching: dict = field(default_factory=dict)  # generic_words, flavour_words (how readable text is weighed)
    coverage_rules: dict = field(default_factory=dict)  # local_model_coverage: which products a local model may cover

    @property
    def identities(self) -> dict[str, str]:
        return {p["identity"]: p["label"] for p in self.products}

    @property
    def by_identity(self) -> dict[str, dict]:
        return {p["identity"]: p for p in self.products}

    @property
    def excluded_by_identity(self) -> dict[str, dict]:
        return {x["identity"]: x for x in self.excluded}

    def label(self, identity) -> str:
        return (self.identities.get(identity) or (self.excluded_by_identity.get(identity) or {}).get("label")
                or self.archived_labels.get(identity) or str(identity))

    def display(self, identity) -> str:
        """The short name for camera summaries ("Bonne orange juice"), else the label."""
        p = self.by_identity.get(identity) or self.excluded_by_identity.get(identity) or {}
        return p.get("display_name") or p.get("label") or self.label(identity)

    def family_display(self, family) -> str:
        f = self.families.get(family) or {}
        return f.get("display_name") or f.get("label") or str(family)

    def family_unit(self, family) -> str | None:
        units = {p.get("count_unit") for p in self.products if p.get("family") == family}
        return units.pop() if len(units) == 1 else None

    def brand_text(self, identity) -> list:
        """The words that name the product's brand on the package (its own, else its flavour family's)."""
        p = self.by_identity.get(identity) or self.excluded_by_identity.get(identity) or {}
        return list(p.get("match_text") or (self.families.get(p.get("family")) or {}).get("brand_text") or [])

    def references(self, store_root, per_product: int = 2) -> list[dict]:
        """Labelled reference photos for visual matching: per active product (and removed product, if any were saved)
        the per_product most recent manual reference crops of the enrolment store. Replays and recognised frames are
        never references."""
        out = []
        root = Path(store_root)
        for p in self.products + self.excluded:
            meta = root / "enrolment" / p["identity"] / "meta.json"
            photos = []
            if meta.exists():
                try:
                    m = json.loads(meta.read_text())
                    photos = sorted((x for x in m.get("photos", []) if x.get("role") == "reference" and x.get("source") == "manual"
                                     and (root / x["path"]).exists()), key=lambda x: x.get("captured_at") or "")[-per_product:]
                except (OSError, ValueError, KeyError):
                    photos = []
            out.append({"identity": p["identity"], "label": p["label"], "display_name": p.get("display_name") or p["label"],
                        "visual_cue": p.get("visual_cue"), "excluded": p["identity"] in self.excluded_by_identity,
                        "photos": [{"path": x["path"], "captured_at": x.get("captured_at"), "file": str(root / x["path"])} for x in photos]})
        return out

    def family_of(self, identity):
        return (self.by_identity.get(identity) or self.excluded_by_identity.get(identity) or {}).get("family")

    def class_kind(self, identity) -> str:
        """What a local model's class name means here: an active product, a removed product (Salted Caramel), an old
        product of an archived set, or unmapped (no meaning known to this set)."""
        if identity in self.by_identity:
            return "active"
        if identity in self.excluded_by_identity:
            return "excluded"
        if identity in self.archived_labels:
            return "archived"
        return "unmapped"

    def coverage(self, model: dict | None) -> dict:
        """The explicit coverage map of a local model (since 15 Nov 06:40; it replaces the gate "every active product
        must be a class"). A model may cover only some products and is never refused for that.

        model: {"name", "classes": the class names in index order from the model's manifest (written only from the
        trainer's export) or None, "num_classes"}. Every class keeps its original index and is either an active
        product, a recognised non-inventory product (removed, e.g. Salted Caramel, or old; never counted, never
        purchasable) or unmapped (its boxes keep their number and the cloud check establishes the identity). An active
        product is covered locally only through a verified class and where local_model_coverage allows it; every
        other active product is covered by the automatic whole-frame cloud check."""
        model = model or {}
        classes = model.get("classes") if isinstance(model.get("classes"), list) else None
        n = model.get("num_classes") or (len(classes) if classes else 0)
        rules = self.coverage_rules.get("products") or {}
        cls = []
        for k in range(int(n or 0)):
            name = classes[k] if classes and k < len(classes) else None
            kind = self.class_kind(name) if name else "unmapped"
            cls.append({"index": k, "name": name, "identity": name if kind != "unmapped" else None, "kind": kind})
        prods = {}
        for p in self.products:
            rule = rules.get(p["identity"]) or {}
            allowed = rule.get("coverage", "local-when-mapped") != "cloud"
            local = [c["index"] for c in cls if c["identity"] == p["identity"]]
            covered = allowed and bool(local)
            why = (f"local class {', '.join(map(str, local))}; cloud check at score <= {p.get('kimi_at_or_below')}" if covered else
                   rule.get("basis") or "cloud-covered by the product file" if not allowed else
                   "no verified local class: covered by the whole-frame cloud check")
            prods[p["identity"]] = {"local": covered, "local_classes": local if covered else [],
                                    "covered_by": "local model + cloud check" if covered else "cloud (whole-frame check)", "why": why}
        return {"model": model.get("name"), "class_map": "verified" if classes else "awaiting trainer metadata",
                "classes": cls, "products": prods,
                "non_inventory_classes": [c["index"] for c in cls if c["kind"] in ("excluded", "archived")],
                "unmapped_classes": [c["index"] for c in cls if c["kind"] == "unmapped"]}

    def family_label(self, family) -> str:
        return (self.families.get(family) or {}).get("label", str(family))

    def by_match_label(self) -> dict[str, str]:
        """The answer words of the text-only matching step -> identity (labels in lower case)."""
        return {p["label"].lower(): p["identity"] for p in self.products}

    def summary(self) -> dict:
        return {"set_id": self.set_id, "schema": self.schema, "sha256": self.sha256, "path": self.path,
                "products": [{k: p.get(k) for k in ("identity", "label", "family", "count_unit", "kimi_at_or_below")}
                             for p in self.products]}

    @classmethod
    def from_dict(cls, d: dict, blob: bytes | None = None, path=None) -> "ProductSet":
        sha = hashlib.sha256(blob).hexdigest() if blob is not None else None
        if d.get("schema") == "shelf-product-set/2":
            arch = {}
            for s in d.get("archived_sets") or []:
                arch.update(s.get("labels") or {})
            for x in d.get("excluded_products") or []:
                arch.setdefault(x["identity"], x["label"])
            return cls(d["set_id"], [dict(p) for p in d["products"]], dict(d.get("families") or {}),
                       dict(d.get("routing") or {}), arch, sha, str(path) if path else None,
                       excluded=[dict(x) for x in d.get("excluded_products") or []],
                       text_matching=dict(d.get("text_matching") or {}),
                       coverage_rules=dict(d.get("local_model_coverage") or {}))
        if d.get("schema") == "shelf-product-set/1":  # archived slot sets: identities and confirmed text as they were
            prods = []
            for v in d.get("slots", {}).values():
                oc = v.get("owner_confirmed") or {}
                prods.append({"identity": v["identity"], "label": v["label"], "family": None,
                              "match_text": oc.get("match_text", []), "variant_text": oc.get("variant_text", []),
                              "confirmed": {k: oc.get(k) for k in ("brand", "variant")} if oc else None})
            return cls(d["set_id"], prods, {}, {}, {}, sha, str(path) if path else None, schema="shelf-product-set/1")
        raise ValueError(f"unknown product-set schema {d.get('schema')!r}")


def read(path) -> ProductSet:
    p = Path(path)
    blob = p.read_bytes()
    return ProductSet.from_dict(json.loads(blob), blob, p)


_lock = threading.Lock()
_cache: dict = {"key": None, "set": None}


def active_path() -> Path:
    env = os.environ.get("SHELF_PRODUCTS", "").strip()
    return Path(env) if env else DEFAULT_ACTIVE


def active() -> ProductSet:
    """The active set, re-read when the file changes. Raises RuntimeError if it is missing or not a v2 set."""
    p = active_path()
    try:
        st = p.stat()
    except OSError as e:
        raise RuntimeError(f"active product set not found at {p}: set SHELF_PRODUCTS to valio-challenge/products/active.json") from e
    key = (str(p), st.st_mtime_ns, st.st_size)
    with _lock:
        if _cache["key"] != key:
            ps = read(p)
            if ps.schema != "shelf-product-set/2":
                raise RuntimeError(f"{p} is not an active (v2) product set")
            _cache.update(key=key, set=ps)
        return _cache["set"]
