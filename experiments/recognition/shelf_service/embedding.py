"""Pinned existing DINOv2-small pooler embedding, on laptop CPU only."""
from pathlib import Path
from time import perf_counter

from recog.models import MODELS

SPEC = MODELS["dinov2-small"]


class DinoEmbedder:
    def __init__(self, cache: Path):
        self.cache = cache
        self.model = self.processor = None
        self.load_ms = None

    @property
    def loaded(self):
        return self.model is not None

    @property
    def info(self):
        return {"name": SPEC["repo"], "revision": SPEC["revision"], "device": "cpu"}

    def load(self):
        if self.loaded:
            return
        import os
        # Keep every model/cache artifact inside the owned private store.
        os.environ["HF_HOME"] = str(self.cache.parent/"hf_home")
        os.environ["HF_XET_CACHE"] = str(self.cache.parent/"hf_xet")
        os.environ["HF_HUB_DISABLE_XET"] = "1"
        import torch
        from transformers import AutoImageProcessor, AutoModel
        start = perf_counter()
        # Bound CPU usage; deterministic evaluation, no MPS assumption.
        torch.set_num_threads(4)
        self.processor = AutoImageProcessor.from_pretrained(
            SPEC["repo"], revision=SPEC["revision"], cache_dir=str(self.cache), use_fast=False, token=False)
        model = AutoModel.from_pretrained(SPEC["repo"], revision=SPEC["revision"],
                                          cache_dir=str(self.cache), token=False).to("cpu").eval()
        self.model = model
        self.load_ms = (perf_counter() - start) * 1000

    def __call__(self, image):
        import torch
        self.load()
        with torch.inference_mode():
            inputs = self.processor(images=[image.convert("RGB")], return_tensors="pt").to("cpu")
            return self.model(**inputs).pooler_output[0].float().cpu().numpy()
