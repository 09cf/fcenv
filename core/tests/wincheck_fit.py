"""Evaluate candidate goal-area (win check) models against real FC results.

The physics is fixed (fccore == fcsim), so only the win check varies: record
every goal block's state for every tick of every design once, then score each
model by the first tick it reports a solve, using ftlib's rules (as in
fc_agreement.py).

usage: python3 tests/wincheck_fit.py [model ...]
"""

import pickle
import sys
from pathlib import Path

import numpy as np

from common import ROOT, cases, excluded
from fc_agreement import run_ticks

import fccore

CACHE = ROOT / "build" / "wincheck_recordings.pkl"


def record(case):
    uid, path, solve, max_ticks, comment = case
    T = run_ticks(solve, max_ticks)
    with fccore.Sim(path.read_bytes()) as sim:
        blocks = sim.blocks()
        goals = [i for i, b in enumerate(blocks) if b.is_goal and not b.is_level]
        g = sim.goal_area()
        arr = sim.record_array(T, blocks=goals) if goals else np.zeros((T, 0, 6))
        info = [(blocks[i].type_id, blocks[i].shape, blocks[i].w, blocks[i].h, blocks[i].radius)
                for i in goals]
    return dict(uid=uid, solve=solve, max_ticks=max_ticks, T=T, comment=comment,
                goal_area=(g.x, g.y, g.w, g.h), goals=info, states=arr[:, :, :3].copy())


def load():
    """Recordings of all tracked designs (excluded.tsv is left out)."""
    skip = excluded()
    return [r for r in _load_all() if r["uid"] not in skip]


def _load_all():
    if CACHE.exists():
        return pickle.loads(CACHE.read_bytes())
    # serial: whole trajectories are large, and parallel workers holding them
    # at once can exhaust memory
    recs = [record(c) for c in cases()]
    CACHE.write_bytes(pickle.dumps(recs))
    return recs


def score(rec, inside_fn):
    """inside_fn(rec) -> bool array (frames,) of 'all goals inside' after each step."""
    if not rec["goals"]:
        won = np.zeros(rec["T"], dtype=bool)
    else:
        won = inside_fn(rec)
    hits = np.flatnonzero(won)
    got = int(hits[0]) + 1 if hits.size else -1
    end = got if got > 0 else rec["T"]
    solve = rec["solve"]
    if solve is None or solve > end:
        return got == -1, got
    return got == solve, got


# ---------------------------------------------------------------- models


def area_bounds(rec):
    gx, gy, gw, gh = rec["goal_area"]
    return gx - gw / 2, gx + gw / 2, gy - gh / 2, gy + gh / 2


def fcsim_model(rec):
    """fcsim/ftlib: rotated rectangle AABB (float32 intermediates), exact centre."""
    x0, x1, y0, y1 = area_bounds(rec)
    ok = np.ones(rec["states"].shape[0], dtype=bool)
    for j, (tid, shape, w, h, r) in enumerate(rec["goals"]):
        x, y, a = rec["states"][:, j].T
        if shape == fccore.SHAPE_CIRCLE:
            ex = ey = np.float64(r)
        else:
            deg = (a * 57.295779513082320876763).astype(np.float32)
            deg = np.where(np.abs(deg) >= 32768, np.float32(-32768), deg)
            rad = (deg * np.float32(0.017453292519943295769245)).astype(np.float32)
            s, c = np.sin(rad).astype(np.float32), np.cos(rad).astype(np.float32)
            wc, ws = np.float32(w) * c, np.float32(w) * s
            hc, hs = np.float32(h) * c, np.float32(h) * s
            ex = (np.abs(wc) + np.abs(hs)).astype(np.float64) / 2
            ey = (np.abs(ws) + np.abs(hc)).astype(np.float64) / 2
        ok &= (x - ex >= x0) & (x + ex <= x1) & (y - ey >= y0) & (y + ey <= y1)
    return ok


def flash_model(quant_pos=None, quant_bounds=None, f32_matrix=True, pad=4.0,
                circle_quant=True, clamp=True, quant_zone=None, quant_circle="same",
                bounds="aabb", clamp_at=32768.0):
    """Player.hasWon() on Flash display-list values.

    Goal rect local bounds are +-(w/2 + pad) x +-(h/2 + pad): radius-3 anchor
    circles with a 2px stroke at the corners. `width` is the AABB of the four
    transformed corners; hasWon subtracts 4 from it.
    quant_pos: how x/y become twips (None, 'trunc', 'round', 'floor').
    quant_bounds: how transformed corners become twips (None, 'round', 'trunc', 'out').
    """
    q = {None: None, "trunc": np.trunc, "round": np.round, "floor": np.floor,
         # round half away from zero
         "round_away": lambda v: np.sign(v) * np.floor(np.abs(v) + 0.5)}

    def twips(v, how):
        return v if how is None else q[how](v * 20) / 20

    def fn(rec):
        # StartZone.getPosition() returns the zone's display x/y (twips);
        # its width/height are plain Numbers
        gx, gy, gw, gh = rec["goal_area"]
        gx, gy = twips(gx, quant_zone), twips(gy, quant_zone)
        x0, x1, y0, y1 = gx - gw / 2, gx + gw / 2, gy - gh / 2, gy + gh / 2
        ok = np.ones(rec["states"].shape[0], dtype=bool)
        for j, (tid, shape, w, h, r) in enumerate(rec["goals"]):
            x, y, a = rec["states"][:, j].T
            if shape == fccore.SHAPE_CIRCLE:
                if circle_quant:
                    qc = quant_pos if quant_circle == "same" else quant_circle
                    x, y = twips(x, qc), twips(y, qc)
                ok &= (x - r >= x0) & (x + r <= x1) & (y - r >= y0) & (y + r <= y1)
                continue
            tx, ty = twips(x, quant_pos), twips(y, quant_pos)
            deg = a * (180 / np.pi)
            if clamp:
                deg = np.where(np.abs(deg) >= clamp_at, -32768.0, deg)
            deg = np.remainder(deg + 180, 360) - 180
            rad = deg * (np.pi / 180)
            ca, sa = np.cos(rad), np.sin(rad)
            if f32_matrix:
                ca, sa = ca.astype(np.float32).astype(np.float64), sa.astype(np.float32).astype(np.float64)
            if bounds == "aabb":
                # transformed local AABB (pad includes anchor radius + stroke)
                A, B = w / 2 + pad, h / 2 + pad
                xs = [ca * sx * A - sa * sy * B for sx in (-1, 1) for sy in (-1, 1)]
                ys = [sa * sx * A + ca * sy * B for sx in (-1, 1) for sy in (-1, 1)]
            else:
                # geometry transformed first, then bounded: corner anchor
                # circles (radius 3) + 1px half stroke. "tight" uses the true
                # circle; "ctrl" uses drawCircle's 8-curve control polygon.
                A, B = w / 2, h / 2
                if bounds == "tight":
                    pts = [(3 * np.cos(k * np.pi / 180), 3 * np.sin(k * np.pi / 180)) for k in range(0, 360, 1)]
                else:
                    rc = 3 / np.cos(np.pi / 8)
                    pts = [(3 * np.cos(k * np.pi / 4), 3 * np.sin(k * np.pi / 4)) for k in range(8)]
                    pts += [(rc * np.cos(np.pi / 8 + k * np.pi / 4), rc * np.sin(np.pi / 8 + k * np.pi / 4)) for k in range(8)]
                xs, ys = [], []
                for sx in (-1, 1):
                    for sy in (-1, 1):
                        for px, py in pts:
                            lx, ly = sx * A + px, sy * B + py
                            xs.append(ca * lx - sa * ly)
                            ys.append(sa * lx + ca * ly)
                pad_stroke = pad - 3  # half stroke beyond the anchor radius
                xs = [np.min(xs, 0) - pad_stroke, np.max(xs, 0) + pad_stroke]
                ys = [np.min(ys, 0) - pad_stroke, np.max(ys, 0) + pad_stroke]
            if quant_bounds == "out":
                xmin = np.floor(np.min(xs, 0) * 20) / 20
                xmax = np.ceil(np.max(xs, 0) * 20) / 20
                ymin = np.floor(np.min(ys, 0) * 20) / 20
                ymax = np.ceil(np.max(ys, 0) * 20) / 20
            else:
                xs = [twips(v, quant_bounds) for v in xs]
                ys = [twips(v, quant_bounds) for v in ys]
                xmin, xmax = np.min(xs, 0), np.max(xs, 0)
                ymin, ymax = np.min(ys, 0), np.max(ys, 0)
            ex = ((xmax - xmin) - 4) / 2
            ey = ((ymax - ymin) - 4) / 2
            ok &= (tx - ex >= x0) & (tx + ex <= x1) & (ty - ey >= y0) & (ty + ey <= y1)
        return ok

    return fn


MODELS = {
    "best": flash_model(quant_pos="trunc", quant_bounds="trunc", quant_zone="trunc"),
    "fcsim": fcsim_model,
    "flash": flash_model(),
    "flash_pos_trunc": flash_model(quant_pos="trunc"),
    "flash_pos_round": flash_model(quant_pos="round"),
    "flash_pos_trunc_b_round": flash_model(quant_pos="trunc", quant_bounds="round"),
    "flash_pos_round_b_round": flash_model(quant_pos="round", quant_bounds="round"),
    "flash_pos_trunc_b_trunc": flash_model(quant_pos="trunc", quant_bounds="trunc"),
    "flash_pos_trunc_b_out": flash_model(quant_pos="trunc", quant_bounds="out"),
}


def grid(recs):
    opts = [None, "trunc", "round", "floor", "round_away"]
    res = []
    for qz in opts:
        for qp in opts:
            for qc in opts:
                for qb in [None, "trunc", "round"]:
                    fn = flash_model(quant_pos=qp, quant_bounds=qb, quant_zone=qz, quant_circle=qc)
                    n_ok = sum(score(r, fn)[0] for r in recs)
                    res.append((n_ok, qz, qp, qc, qb))
    for n_ok, qz, qp, qc, qb in sorted(res, key=lambda r: -r[0])[:12]:
        print(f"{n_ok}/{len(recs)} zone={qz} rect_pos={qp} circle_pos={qc} bounds={qb}")


def evaluate(recs, name, fn, verbose=False):
    bad = []
    for rec in recs:
        ok, got = score(rec, fn)
        if not ok:
            bad.append((rec["uid"], rec["solve"], rec["max_ticks"], got, rec["comment"]))
    n = len(recs)
    print(f"{name:28s} {n - len(bad)}/{n} ({100 * (n - len(bad)) / n:.1f}%)")
    if verbose:
        for b in bad:
            print("   ", *b)
    return bad


def main():
    recs = load()
    if "--grid" in sys.argv:
        return grid(recs)
    names = [a for a in sys.argv[1:] if not a.startswith("-")] or list(MODELS)
    for name in names:
        evaluate(recs, name, MODELS[name], verbose="-v" in sys.argv)


if __name__ == "__main__":
    main()
