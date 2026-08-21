"""INT8 quantization of the segmentation classifier's feature-space centroids
(`app/perception/segment.py`'s `CENTROID_FEATURES`) -- the ONE place in this
pipeline with float "model" parameters. Depth (SGBM/numpy slope-roughness)
and SLAM (numpy SSD registration) have no learned weights at all -- see
`docs/architecture/edge-ai.md` for why their shrink lever is input-resolution
/ matcher-window knobs, not quantization.

Scheme: per-feature-dimension affine (asymmetric) quantization to int8
[-128, 127], calibrated on the 8 class centroids themselves (the entire
"training set" this classifier has -- there is no held-out calibration set
because there is no learned model, just 8 fixed reference colours):

    scale_d      = (hi_d - lo_d) / 255
    zero_point_d = round(-lo_d / scale_d) - 128
    q            = clip(round(x / scale_d) + zero_point_d, -128, 127)
    dequant(q)   = (q - zero_point_d) * scale_d

Nearest-centroid distance is computed on DEQUANTIZED int8 codes (standard
affine-quantization practice: values are stored/compared as int8, distance
arithmetic is scale-corrected) so the precision loss is real and measured,
not hidden behind float shadow state.

Offline tooling only -- NOT imported by main.py (the deployed API never
touches this module; see the edge-ai skill's measurement/decision boundary).
Read-only over segment.py: imports its constants/helpers, never edits them.
numpy + stdlib only, no new deps.

Run:  python -m app.edge.quantize   (from backend/)
"""
from __future__ import annotations
import json
from pathlib import Path

import numpy as np

from app.contracts import SegCell
from app.perception.segment import (
    CLASS_COLORS, CENTROID_FEATURES, _features, classify,
    CONF_CAP, CONF_FLOOR, UNKNOWN_DIST, SHADOW_MAX_RGB,
)

_NAMES = list(CENTROID_FEATURES)
_FEAT = np.array([CENTROID_FEATURES[n] for n in _NAMES], dtype=np.float64)  # (8, 6) float64

# --- per-dimension affine int8 calibration, fit on the centroids themselves ---
_LO = _FEAT.min(axis=0)
_HI = _FEAT.max(axis=0)
_SCALE = np.where(_HI > _LO, (_HI - _LO) / 255.0, 1.0)  # avoid /0 on a constant dim
_ZERO_POINT = np.round(-_LO / _SCALE).astype(np.int32) - 128


def _quantize(f) -> np.ndarray:
    """feature tuple -> int8[6], saturating at the calibrated centroid range."""
    q = np.round(np.asarray(f, dtype=np.float64) / _SCALE) + _ZERO_POINT
    return np.clip(q, -128, 127).astype(np.int8)


def _dequantize(q: np.ndarray) -> np.ndarray:
    return (q.astype(np.float64) - _ZERO_POINT) * _SCALE


CENTROID_CODES = {n: _quantize(CENTROID_FEATURES[n]) for n in _NAMES}  # int8, 8*6 = 48 bytes

# Conservatism derate on the quantized confidence margin: rounding can
# occasionally make two originally-distinct float features snap to the same
# int8 bucket and look MORE confident than the float classifier did -- this
# keeps the int8 path from ever claiming more certainty than quantization
# actually bought it (the no-false-safe requirement extends to confidence,
# not just class label: scoring.py shrinks low conf toward neutral, so an
# over-confident quantized label is exactly the failure mode to avoid).
_MARGIN_DERATE = 0.9


def classify_int8(rgb) -> SegCell:
    """Same contract as segment.classify(rgb): (class, conservative conf),
    computed against the quantized centroids instead of the float ones."""
    if max(rgb) <= SHADOW_MAX_RGB:
        return SegCell("shadow", CONF_CAP)
    f = _features(rgb)
    dq = _dequantize(_quantize(f))
    dists = sorted(
        ((n, float(np.linalg.norm(dq - _dequantize(CENTROID_CODES[n])))) for n in _NAMES),
        key=lambda kv: kv[1],
    )
    c1, d1 = dists[0]
    if d1 > UNKNOWN_DIST:
        return SegCell("unknown", CONF_FLOOR)
    d2 = dists[1][1]
    margin = 0.0 if d2 == 0 else max(0.0, (d2 - d1) / d2)
    conf = round(min(CONF_CAP, CONF_FLOOR + CONF_CAP * margin * _MARGIN_DERATE), 3)
    return SegCell(c1, conf)


def _memory_report() -> str:
    float_bytes = _FEAT.nbytes  # 8 classes * 6 dims * 8 bytes (float64)
    code_bytes = sum(c.nbytes for c in CENTROID_CODES.values())  # int8
    quant_param_bytes = _SCALE.nbytes + _ZERO_POINT.nbytes  # shared calibration, amortized once
    return (
        f"centroid storage: float64={float_bytes}B -> int8={code_bytes}B "
        f"({float_bytes / code_bytes:.1f}x smaller); "
        f"+{quant_param_bytes}B one-time scale/zero-point table "
        f"(int8+params={code_bytes + quant_param_bytes}B, "
        f"{float_bytes / (code_bytes + quant_param_bytes):.1f}x smaller than float64 alone)"
    )


CONF_EPS = 0.05  # small slack for quantization rounding noise, per task spec
HAZARD_CLASSES = {"crater", "rock"}  # zone-3-relevant classes (scoring.zone precedence)


def _demo():
    # 1) every class centroid: int8 path agrees with float, conf conservative
    for name, rgb in CLASS_COLORS.items():
        got_f = classify(rgb)
        got_q = classify_int8(rgb)
        assert got_q.terrain_class == got_f.terrain_class, (
            f"centroid {name}: float={got_f.terrain_class} int8={got_q.terrain_class}")
        assert 0.0 <= got_q.conf <= 1.0, got_q
        assert got_q.conf <= got_f.conf + CONF_EPS, (
            f"centroid {name}: int8 conf {got_q.conf} exceeds float conf "
            f"{got_f.conf} + {CONF_EPS} -- quantization must not be MORE confident")

    # 2) scene_0 fixture: every cell keeps its class under quantization; no
    #    hazard-relevant cell (crater/rock) is ever allowed to flip class.
    scene_path = Path(__file__).resolve().parent.parent.parent / "mock/fixtures/scene_0/scene.json"
    scene = json.loads(scene_path.read_text())
    n_cells = 0
    n_hazard = 0
    for row in scene["rgb"]:
        for rgb in row:
            n_cells += 1
            f = classify(tuple(rgb))
            q = classify_int8(tuple(rgb))
            if f.terrain_class in HAZARD_CLASSES:
                n_hazard += 1
                assert q.terrain_class == f.terrain_class, (
                    f"FALSE-SAFE RISK: hazard class {f.terrain_class!r} at rgb={rgb} "
                    f"flipped to {q.terrain_class!r} under int8 quantization")
            assert q.terrain_class == f.terrain_class, (
                f"cell rgb={rgb}: float={f.terrain_class} int8={q.terrain_class} (class flip)")
            assert 0.0 <= q.conf <= 1.0, q
            assert q.conf <= f.conf + CONF_EPS, (
                f"cell rgb={rgb}: int8 conf {q.conf} exceeds float conf {f.conf} + {CONF_EPS}")

    print(f"agreement: {len(CLASS_COLORS)} centroids + {n_cells} scene_0 cells, "
          f"0 class flips, {n_hazard} hazard-class cell(s) verified stable")
    print(_memory_report())
    print("quantize self-check ok")


if __name__ == "__main__":
    _demo()
