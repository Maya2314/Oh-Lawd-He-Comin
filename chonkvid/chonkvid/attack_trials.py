"""Measure every catalogue attack the same way ChonkVid is measured.

For each attack: n one-hop trials with the payload (does the agent's next message
carry it on?) plus a control arm (the same messages without it, for the detector's
false-positive rate). The attack's own effect (obeyed / leaked) is counted alongside.
Results go to results/attacks-latest.json and are served at /api/attacks.

    python -m chonkvid attacks -n 20
"""

from __future__ import annotations

import json

from . import attacks
from .detect import Detector
from .experiment import RESULTS
from .trials import run_trials

LATEST = "attacks-latest.json"


def _slim(t: dict) -> dict:
    """A trials result without the per-reply records, plus up to two example replies."""
    out = {k: v for k, v in t.items() if k != "records"}
    out["examples"] = [r["reply"][:400] for r in t["records"] if r["verdict"]["infected"]][:2]
    return out


async def measure_attacks(llm, s, n: int = 20, ids: list[str] | None = None,
                          system: str | None = None) -> dict:
    det = Detector(llm, s.threshold)
    chosen = [attacks.ATTACKS[i] for i in (ids or list(attacks.ATTACKS))]
    rows = {}
    for i, atk in enumerate(chosen):
        seed = 101 + i
        base = await run_trials(llm, det, n, None, label=f"{atk.name}: payload", seed=seed,
                                agent_model=s.agent_model, system=system, attack=atk,
                                vision_model=s.vision_model)
        ctrl = await run_trials(llm, det, max(5, n // 2), None, with_payload=False,
                                label=f"{atk.name}: control", seed=seed,
                                agent_model=s.agent_model, system=system, attack=atk,
                                vision_model=s.vision_model)
        rows[atk.id] = {
            "attack": atk.summary(),
            "model": s.vision_model if atk.channel == "image" else s.agent_model,
            "spread": _slim(base),
            "control": _slim(ctrl),
            "effect_rate": base["effect_hits"] / base["n"] if base["n"] else 0.0,
        }
    return {"source": llm.source, "n": n, "threshold": s.threshold,
            "system": system or "default work prompt", "attacks": rows}


def save(out: dict) -> None:
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / LATEST).write_text(json.dumps(out, indent=2))


def load() -> dict | None:
    p = RESULTS / LATEST
    return json.loads(p.read_text()) if p.is_file() else None


def table(out: dict) -> str:
    lines = [f"[{out['source']}] spread per contact by attack, n={out['n']} per arm, "
             f"threshold {out['threshold']}",
             f"{'attack':18s} {'channel':8s} {'spread p':>9s}  {'95% CI':>13s}  "
             f"{'control':>8s}  effect"]
    for r in out["attacks"].values():
        a, t, c = r["attack"], r["spread"], r["control"]
        lines.append(f"{a['name']:18s} {a['channel']:8s} {t['k']:>3d}/{t['n']:<3d} {t['p']:.2f}  "
                     f"[{t['ci'][0]:.2f}, {t['ci'][1]:.2f}]  {c['k']:>3d}/{c['n']:<3d}  "
                     f"{a['effect']} {r['effect_rate']:.0%}")
    if out["source"] == "mock":
        lines.append("MOCK: scripted stand-in model; not a measurement of any Mistral model.")
    return "\n".join(lines)
