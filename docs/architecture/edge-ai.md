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

So Phase 5 today is two things: (1) measure what actually runs, against a
documented compute envelope, and (2) write down the quantization/backbone plan
to execute **when** a trained model replaces a classical stage — not before.

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

## Quantization + backbone plan — DEFERRED until a model lands

There is nothing to quantize yet. This section is the plan to execute the
day segmentation (the one stage plausibly replaced by a learned model per
`docs/implementation-plan.md`'s P7 note) gets a trained backbone — written now
so that day isn't a blank slate, not because any of it is buildable today.

**Entry criteria (all must hold before this plan activates):**
1. A trained segmentation model exists (checkpoint + eval numbers against
   `app/eval/metrics.py`'s IoU/mIoU on real or better-than-synthetic labels).
2. The classical HSV classifier's IoU is the documented baseline the model
   must beat — replacing it without a measured accuracy win is a regression,
   not progress.
3. `torch`/export tooling is added to `requirements-cv.txt` only (never
   `requirements.txt` — the deployed API stays torch-free) at that point, not
   before.

**Then, in order:**
1. **Backbone:** a MobileNet-class (or equivalent depthwise-separable, e.g.
   MobileNetV3-small / a small EfficientNet variant) encoder — chosen for
   parameter count and on-device FLOPs, not accuracy alone, matching this
   skill's "compact, quantization-friendly" guidance. Full benchmark
   (accuracy vs. latency vs. size) against 1-2 alternatives before locking
   the choice in.
2. **Export:** PyTorch -> ONNX, static input shape (the tile grid size is
   fixed per deployment, no need for dynamic-shape export overhead).
3. **Quantization:** post-training static INT8 quantization (calibrated on a
   representative held-out set from `backend/data/`) as the default target;
   fall back to dynamic/FP16 only if INT8 fails the accuracy-vs.-baseline
   gate. Quantization-aware training only if PTQ misses the accuracy bar.
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

**Explicitly out of scope for this phase:** no backbone has been chosen, no
ONNX export exists, no quantization has been run, no `torch`/`onnxruntime`
dependency has been added anywhere. Adding any of that now would be
scaffolding for a model that doesn't exist.

## Self-check

```
cd backend && python -m app.edge.budget
```
Prints the per-stage ms/KiB breakdown on `scene_0` and asserts each stage
stays under its generous smoke ceiling (see `BUDGET_MS`/`BUDGET_PEAK_KIB` in
`app/edge/budget.py` for why those ceilings are proxies, not the rover target
table above).
