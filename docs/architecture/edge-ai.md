# TerraSight — Edge / On-Rover Optimization (Phase 5)

Companion to `SYSTEM.md`. Scope per the `edge-ai` skill: deployment *shape* of
the existing pipeline (latency, memory, power), not new functionality.

## Reality check — there is no model yet

Segmentation (`app/perception/segment.py`) is an HSV/opponent-colour classifier
against fixed centroids. Depth geometry (`app/depth/pipeline.py`) is a numpy
central-difference slope/roughness computation over a height grid (fed by
`cv2.StereoSGBM` in `app/depth/stereo.py`, itself classical block matching, no
learned weights). SLAM (`app/slam/pose.py`, `app/slam/fuse.py`) is a numpy SSD
grid-shift search. **None of this is a neural network.** `torch` /
`onnxruntime` / `segmentation_models_pytorch` are deliberately absent from
`requirements-cv.txt` ("added when the model lands").

So Phase 5 is three things: (1) measure what actually runs, against a
documented compute envelope; (2) quantize the one thing with float
parameters today — the segmentation classifier's centroids (`app/edge/
quantize.py`, done, see below); and (3) decide + document the backbone/
quantization plan to execute **when** a trained model replaces a classical
stage — not before.

## Target on-rover compute envelope

Proxy numbers for a rad-hard/rad-tolerant rover-class SBC (e.g. an
RAD750-class or a modern rad-tolerant ARM COTS board flown with margin) — not
measured on real flight hardware, no such hardware is available to this repo.
Treat these as the design input the smoke ceilings in `app/edge/budget.py`
stand in for, not as validated numbers:

| Resource | Budget | Rationale |
|---|---|---|
| CPU class | single/dual-core, ~100-400 MHz effective (rad-hard) equivalent | rad-hardening trades clock speed for radiation tolerance; no GPU assumed |
| RAM | ~256 MB total, low tens of MB for perception | shared with nav/comms/power management on a rover-class stack |
| Power | perception duty cycle should stay in the low single-digit watts | solar/battery-constrained, no thermal headroom for sustained high draw |
| Frame cadence | 1 frame every 1-5 s (drive-and-stop, not video-rate) | "no waiting on Earth" means bounded *local* latency, not high frame rate — the rover proceeds once a frame is scored, it doesn't need 30fps |

## Per-stage latency/memory budgets

Measured by `backend/app/edge/budget.py` on the `scene_0` fixture (27 cells,
3x9 grid) on an Earth dev machine — see that module's docstring/comments for
why the *smoke* ceilings asserted there (`BUDGET_MS` / `BUDGET_PEAK_KIB`) are
generous proxies, not rover-validated numbers. Measured figures (one run,
2026-08-20, dev laptop):

| Stage | Median (ms) | Peak (KiB) |
|---|---|---|
| segmentation | ~1.0 | ~6 |
| depth_geometry | ~1.3 | ~4 |
| slam_fuse (single-frame) | ~0.03 | ~4 |
| terrain_assembly | ~0.15 | ~3 |
| full `pipeline.run` | ~5.5 | ~45 |

At `scene_0`'s scale these are noise-floor numbers, not a meaningful rover
estimate — the fixture is 27 cells; a real rover tile grid is orders of
magnitude larger. The harness exists so re-running it against a larger fixture
or real captured geometry gives an honest, reproducible before/after number
instead of an assumed one. **Report measured numbers, not assumed ones** (per
the `edge-ai` skill) — re-run `python -m app.edge.budget` after any stage
change and compare, don't guess.

Scaling risk by stage (why each stage's growth rate matters going forward):
- `segmentation`: O(rows * cols) per-pixel classify + one O(1) 3x3 texture
  pass via numpy — linear, cheap to extrapolate.
- `depth_geometry`: O(rows * cols), one-sided gradient + 3x3 window — linear.
- `slam_fuse` (single-frame passthrough): O(rows * cols) — linear. The
  multi-frame `fuse_sequence`/`track_poses` path (used once real multi-frame
  traverses exist, not by today's single-frame `pipeline.run`) is
  `O(frames * search_radius^2 * cells)` from the SSD shift search — bounded by
  `SEARCH_RADIUS_DEFAULT`, worth re-profiling once that path is wired into the
  runner.
- `terrain_assembly`: `extract_boundaries`'s border walk is documented O(n^2)
  greedy nearest-neighbour (`app/terrain/assemble.py`, ponytail comment
  in-file) — fine per-scene today, the first thing to revisit if terrain
  grids grow into the thousands of cells.

## Frame-cadence strategy (real-time without a heavier model)

Given the classical pipeline is already linear and sub-millisecond per stage
at fixture scale, the near-term "real-time on rover-class compute" lever is
*not* raw stage speed — it's not re-running full-cost stages on frames that
carry no new information:

- **Frame-diff skip:** if consecutive raw frames (RGB and/or height grid) are
  near-identical (cheap mean-abs-diff below a threshold), skip re-running
  segmentation + depth geometry and reuse the last `FusedCell` grid — a
  stationary or slow-drive rover doesn't need to re-classify unchanged
  terrain every tick.
- **Reduced cadence under high confidence:** once `rover_path` mode is
  `full` (see `slam/pose.py`'s degradation ladder) over a stable stretch,
  the caller (rover control loop, outside this repo's scope) can safely widen
  the interval between full pipeline runs; degrade back to full cadence the
  moment pose confidence drops.
- **Static-scene skip is a caller/scheduling decision, not a pipeline
  change:** it belongs in whatever loop invokes `pipeline.run` per frame, not
  in `segment`/`derive_geometry`/`fuse_single` themselves — those stay pure
  measurement functions with no frame-history state, matching the
  measurement/decision boundary already enforced elsewhere in this pipeline.
  **Not implemented in this phase** — no frame-history/scheduling code has
  been added; this is scoped design for whenever the runner grows a live
  frame loop.

## Segmentation quantization — DONE (`app/edge/quantize.py`)

The segmentation classifier (`app/perception/segment.py`) is the one stage
with actual float "model" parameters: `CENTROID_FEATURES`, the 8 class-colour
centroids in 6-D HSV/opponent-colour feature space. `app/edge/quantize.py`
quantizes them to **INT8** and proves the quantized path is safe to swap in:

- **Scheme:** per-feature-dimension affine (asymmetric) quantization to int8
  `[-128, 127]`, calibrated on the 8 centroids themselves (`scale_d = (hi_d -
  lo_d) / 255`, `zero_point_d` solved so the calibrated range round-trips).
  Nearest-centroid distance is computed on dequantized int8 codes (the
  standard affine-quantization pattern: store/compare in int8, arithmetic is
  scale-corrected), so the precision loss is real and measured, not hidden.
- **`classify_int8(rgb)`** returns the same `SegCell` contract as
  `segment.classify`, with a conservatism derate (`_MARGIN_DERATE = 0.9`) on
  the confidence margin so quantization can only make the classifier *less*
  confident, never more — matching the no-false-safe requirement's extension
  to confidence, not just class label.
- **Measured shrink** (`python -m app.edge.quantize`, 2026-08-21): centroid
  storage float64 384B -> int8 48B (**8.0x smaller**); including the one-time
  shared scale/zero-point calibration table (72B), int8+params = 120B
  (**3.2x smaller** than float64 alone). Absolute numbers are tiny because
  this "model" is 8 centroids, not a trained net — the scheme is what
  transfers to a real backbone's weights, not the byte count.
- **Agreement / no-false-safe proof:** self-check asserts `classify_int8`
  matches `classify`'s class on all 8 centroids AND every cell of the
  `scene_0` fixture (27 cells) — **0 class flips**, including the 4
  hazard-relevant (`crater`/`rock`) cells checked explicitly, and int8 conf
  never exceeds float conf by more than a small epsilon (0.05). A class flip
  on a hazard cell fails the check loudly (`AssertionError` naming the
  cell), which is the concrete no-false-safe gate for this module.
- **Not wired into `main.py`** — `app/edge/` is offline tooling only, per the
  edge-ai skill's measurement/decision boundary. `classify_int8` is a
  benchmarked candidate for the live path, not a live swap; swapping it in
  is a separate, explicit decision gated on `test_safety_regression.py`
  staying green, same as the backbone swap gate below.

## Depth / SLAM shrink strategy — quantization N/A, no learned weights

`app/depth/pipeline.py` (numpy central-difference slope/roughness) and
`app/slam/pose.py` / `fuse.py` (numpy SSD grid-shift search) have **zero
learned parameters** — there is nothing to quantize. Their shrink levers are
different knobs, already exposed and already the right ones to tune first:

- **Input-resolution downscale:** both stages are `O(rows * cols)` (see the
  scaling-risk table above); halving the tile grid resolution before depth/
  SLAM roughly quarters their compute and memory, at a measurable cost to
  slope/roughness fidelity — trade accuracy vs. cost the same way PTQ trades
  it, just on the input grid instead of on weights.
- **Matcher/search-radius knobs:** `slam/pose.py`'s `SEARCH_RADIUS_DEFAULT`
  bounds the `O(frames * search_radius^2 * cells)` multi-frame SSD search;
  shrinking it directly shrinks that stage's cost, same shape as reducing a
  detector's proposal count. `depth/stereo.py`'s SGBM disparity range/block
  size (when the real `cv2` path is used instead of the height-grid stand-in)
  is the equivalent knob for depth.
- Any future re-profiling of these knobs is a `budget.py`-style
  before/after measurement (extend `app/edge/budget.py` with a downscaled
  variant of `scene_0` or a larger fixture), not quantization work — kept
  out of `app/edge/quantize.py`'s scope, which is centroid-quantization only.

## Backbone choice — DECIDED (target, not yet built)

**No trained model exists today** — the "backbone" running in production is
the classical stack above (HSV/opponent-colour centroids for segmentation,
numpy slope/roughness for depth, numpy SSD registration for SLAM), chosen
because it is exactly reproducible, has zero cold-start/inference cost, and
needs no training data. This IS the interim backbone, not a placeholder that
does nothing.

**Target NN backbone, decided in advance for when a trained model lands**
(entry criteria below gate *building* it, not this decision):

- **MobileNetV3-Small** encoder for segmentation. Rationale: depthwise-
  separable convolutions are the cheapest per-parameter accuracy on
  CPU-only, no-GPU rover-class compute (matches the edge-ai skill's
  "compact, quantization-friendly" guidance directly); it's a well-trodden
  INT8 PTQ target (used as the reference case for the quantization scheme
  above); its ~2.5M parameter / ~60M FLOP footprint is small enough that
  even an unoptimized FP32 forward pass is plausible within the per-stage
  latency budget on the target compute envelope, leaving INT8 headroom
  rather than requiring it just to fit.
- Alternatives considered and rejected for now: a full EfficientNet variant
  (more accurate, more FLOPs than the compute envelope's headroom
  justifies without a measured accuracy win over MobileNetV3-Small); a
  ResNet-class encoder (standard convolutions, worse FLOPs/parameter on
  CPU, no offsetting accuracy case for this classifier's small taxonomy).
- This is a decision to build, not a build: **no ONNX export exists, no
  PyTorch model exists, no `torch`/`onnxruntime` dependency has been added.**
  Adding those now, before a model is trained, would be scaffolding.

**Entry criteria (all must hold before building the backbone above):**
1. A trained segmentation model exists (checkpoint + eval numbers against
   `app/eval/metrics.py`'s IoU/mIoU on real or better-than-synthetic labels).
2. The classical HSV classifier's IoU is the documented baseline the model
   must beat — replacing it without a measured accuracy win is a regression,
   not progress.
3. `torch`/export tooling is added to `requirements-cv.txt` only (never
   `requirements.txt` — the deployed API stays torch-free) at that point, not
   before.

**Then, in order:**
1. Train/fine-tune MobileNetV3-Small on the segmentation taxonomy.
2. **Export:** PyTorch -> ONNX, static input shape (the tile grid size is
   fixed per deployment, no need for dynamic-shape export overhead).
3. **Quantization:** post-training static INT8 (same affine-quantization
   family as `app/edge/quantize.py` above, extended from 8 centroids to a
   full network's weights and calibrated on a representative held-out set
   from `backend/data/`) as the default target; fall back to dynamic/FP16
   only if INT8 fails the accuracy-vs.-baseline gate. Quantization-aware
   training only if PTQ misses the accuracy bar.
4. **Runtime:** ONNX Runtime (CPU execution provider, no GPU assumed per the
   compute envelope above) — chosen over a raw PyTorch runtime for its
   smaller footprint and INT8 kernel support, and over TFLite because the
   rest of this pipeline's export path is PyTorch-native.
5. **Re-run `app/edge/budget.py`-style profiling** (extended with a model
   stage once it exists) on the same real hardware class this doc's envelope
   targets, and gate the swap on: (a) IoU >= classical baseline, (b) latency
   within the per-stage budget above, (c) `test_safety_regression.py` still
   green — a model output never overrides `scoring.py`'s deterministic rules
   (see `CLAUDE.md`), it only replaces the classical `SegCell` producer
   feeding into the same frozen `contracts.SegCell` shape.

## Self-check

```
cd backend && python -m app.edge.budget
```
Prints the per-stage ms/KiB breakdown on `scene_0` and asserts each stage
stays under its generous smoke ceiling (see `BUDGET_MS`/`BUDGET_PEAK_KIB` in
`app/edge/budget.py` for why those ceilings are proxies, not the rover target
table above).

```
cd backend && python -m app.edge.quantize
```
Prints the measured centroid memory shrink and asserts `classify_int8`
agrees with `classify` on all 8 class centroids and every `scene_0` cell —
zero class flips, hazard cells (`crater`/`rock`) checked explicitly, int8
confidence never exceeds float confidence by more than 0.05. Fails loudly
(naming the cell) on any hazard-class flip — the concrete no-false-safe gate
for the quantized path.
