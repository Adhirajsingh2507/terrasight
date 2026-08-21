"""Latency/memory budget harness for the CURRENT (classical, no-NN) perception
pipeline — makes the "no waiting on Earth" real-time requirement measurable.

TerraSight has no trained model yet (see docs/architecture/edge-ai.md): seg is
an HSV colour classifier, depth geometry is numpy slope/roughness math, SLAM
fusion is numpy registration. Nothing here quantizes a model, because there is
no model to quantize — this profiles the classical stages that exist today, so
we have real numbers before a trained backbone (and its quantization plan)
ever lands.

Offline profiling tool: NOT imported by `main.py` / the deployed API. Pure
stdlib (`time`, `tracemalloc`) + numpy (already required by perception/slam).
Never imports cv2 — `depth/stereo.py`'s real SGBM path is guarded/optional
there for the same reason and is out of scope for this always-run harness.

Run:  python -m app.edge.budget   (from backend/)
"""
from __future__ import annotations
import time
import tracemalloc
from dataclasses import dataclass

from app import pipeline as pipeline_mod
from app.perception.segment import segment
from app.depth.pipeline import derive_geometry
from app.slam.fuse import fuse_single
from app.terrain.assemble import build_cells, extract_boundaries

# ---------------------------------------------------------------------------
# Budgets
# ---------------------------------------------------------------------------
# ponytail: these are GENEROUS SMOKE ceilings on whatever dev machine runs the
# self-check, not the real rad-hard rover CPU target. An Earth dev laptop has
# no thermal/power throttling and is not representative of rover-class
# compute -- so a tight ceiling here would be flaky across machines, not a
# meaningful edge-readiness check. These only catch a stage regressing by
# 10-100x (e.g. an accidental O(n^3) loop landing in a "measurement" stage).
# The real on-rover latency/power/memory targets these numbers are a proxy
# for live in docs/architecture/edge-ai.md — that's the number to tune
# against once real rover-class hardware (or an accurate emulator) is
# available, not this file.
BUDGET_MS = {
    "segmentation": 200.0,
    "depth_geometry": 200.0,
    "slam_fuse": 100.0,
    "terrain_assembly": 100.0,
    "pipeline_run": 1000.0,
}
BUDGET_PEAK_KIB = {
    "segmentation": 20_000.0,
    "depth_geometry": 20_000.0,
    "slam_fuse": 10_000.0,
    "terrain_assembly": 10_000.0,
    "pipeline_run": 50_000.0,
}

STAGE_REPEATS = 50    # scene_0 is tiny; repeat for a stable median timing
RUN_REPEATS = 10       # pipeline.run() re-does every stage; fewer repeats


@dataclass
class StageResult:
    name: str
    ms: float        # median wall time per call, across repeats
    peak_kib: float  # peak traced allocation for one call


def _median_ms(fn, repeats: int) -> float:
    times = []
    for _ in range(repeats):
        t0 = time.perf_counter()
        fn()
        times.append((time.perf_counter() - t0) * 1000.0)
    times.sort()
    return times[len(times) // 2]


def _peak_kib(fn) -> float:
    tracemalloc.start()
    try:
        fn()
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return peak / 1024.0


def _time_stage(name: str, fn, repeats: int = STAGE_REPEATS) -> StageResult:
    return StageResult(name, _median_ms(fn, repeats), _peak_kib(fn))


def run_budget(scene_name: str = "scene_0") -> list[StageResult]:
    """Times each pipeline stage (and the full `pipeline.run`) on `scene_name`.
    Read-only over the other stages: imports and calls them, never edits."""
    scene = pipeline_mod.load_scene(scene_name)
    seg = segment(scene["rgb"])
    depth = derive_geometry(scene["heights"], scene["cell_size_m"])
    fused = fuse_single(seg, depth)

    def _terrain():
        build_cells(fused, scene["cell_size_m"])
        extract_boundaries(fused)

    return [
        _time_stage("segmentation", lambda: segment(scene["rgb"])),
        _time_stage("depth_geometry",
                    lambda: derive_geometry(scene["heights"], scene["cell_size_m"])),
        _time_stage("slam_fuse", lambda: fuse_single(seg, depth)),
        _time_stage("terrain_assembly", _terrain),
        _time_stage("pipeline_run", lambda: pipeline_mod.run(scene_name),
                    repeats=RUN_REPEATS),
    ]


def _print_report(results: list[StageResult]) -> None:
    print(f"{'stage':<18}{'ms (median)':>14}{'peak KiB':>14}")
    for r in results:
        print(f"{r.name:<18}{r.ms:>14.4f}{r.peak_kib:>14.1f}")


def _demo():
    results = run_budget()
    _print_report(results)
    for r in results:
        assert r.ms < BUDGET_MS[r.name], (
            f"{r.name}: {r.ms:.2f}ms exceeds smoke ceiling {BUDGET_MS[r.name]}ms")
        assert r.peak_kib < BUDGET_PEAK_KIB[r.name], (
            f"{r.name}: {r.peak_kib:.1f}KiB exceeds smoke ceiling "
            f"{BUDGET_PEAK_KIB[r.name]}KiB")
    print("edge budget self-check ok")


if __name__ == "__main__":
    _demo()
