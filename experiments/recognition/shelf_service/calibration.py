"""Calibration accepts reference vectors and background negatives only, never heldout paths."""
import numpy as np
from recog.core import match, normalise


def calibrate(vectors, labels, negatives, fingerprints=()):
    base = {"source": "reference leave-one-out + empty-background non-product crops only",
            "reference_fingerprints": list(fingerprints), "reference_count": len(labels),
            "negative_count": len(negatives), "heldout_used": False,
            "policy": "reject above hardest negative; accept near weakest LOO positive; half weakest positive gap",
            "accept": 1.0001, "margin": 1.0, "ready": False}
    if not labels or not negatives or any(labels.count(label) < 2 for label in set(labels)):
        return {**base, "reason": "need >=2 distinct references per enrolled identity and background negatives"}
    gallery = normalise(np.array(vectors))
    positives, gaps, impostors = [], [], []
    for i, label in enumerate(labels):
        sims = gallery @ gallery[i]
        positive = max(float(sims[j]) for j in range(len(labels)) if j != i and labels[j] == label)
        wrong = max([float(sims[j]) for j in range(len(labels)) if labels[j] != label] or [-1.0])
        positives.append(positive)
        gaps.append(positive - wrong)
        impostors.append(wrong)
    negative_scores = (normalise(np.array(negatives)) @ gallery.T).max(axis=1).tolist()
    hardest = max(negative_scores + impostors)
    # Constants are fixed engineering guard bands, not fitted to evaluation or unknown products.
    accept = min(1.0001, max(hardest + .01, min(positives) - .02))
    margin = max(.01, min(gaps) / 2)
    return {**base, "accept": accept, "margin": margin, "ready": True,
            "loo_positive_min": min(positives), "loo_gap_min": min(gaps),
            "hardest_negative": hardest, "separable": min(positives) > hardest}


def classify(vector, vectors, labels, thresholds):
    if not labels:
        return {"status": "unknown", "identity": None, "candidates": []}
    similarities = normalise(np.array(vectors)) @ normalise(vector)
    best = {}
    for label, similarity in zip(labels, similarities.tolist()):
        best[label] = max(best.get(label, -1.0), similarity)
    ranked = sorted(best.items(), key=lambda pair: pair[1], reverse=True)
    candidates = [{"identity": label, "similarity": round(score, 6)} for label, score in ranked[:3]]
    decision = match(vector, np.array(vectors), labels, thresholds['accept'], thresholds['margin'])
    if decision['label'] != 'unknown' and thresholds['ready']:
        status, identity = "known", decision['label']
    elif thresholds['ready'] and ranked[0][1] >= thresholds['accept'] and len(ranked) > 1 and ranked[0][1]-ranked[1][1] < thresholds['margin']:
        status, identity = "ambiguous", None
    else:
        status, identity = "unknown", None
    return {"status": status, "identity": identity, "candidates": candidates}
