"""One-hop transmission trials: the measurement at the heart of ChonkVid.

Give an agent a colleague's message that carries the payload and check whether its
next message carries it on. Repeat n times -> a binomial per-contact spread
probability with a Wilson 95% CI. The control arm sends the same message without the
payload and measures the detector's false-positive rate."""

from __future__ import annotations

import asyncio
import random

from . import scenario
from .detect import Detector
from .stats import wilson
from .world import Agent


def _agent(rng: random.Random, i: int) -> Agent:
    return Agent(id=i, name=rng.choice(scenario.FIRST_NAMES), role=rng.choice(scenario.ROLES),
                 topic=rng.choice(scenario.TOPICS), household=0, workplace=0, venue=False)


async def run_trials(llm, detector: Detector, n: int, vaccine: str | None,
                     with_payload: bool = True, seed: int = 0, label: str = "",
                     agent_model: str = "", system: str | None = None,
                     attack=None, vision_model: str = "") -> dict:
    """`attack`: an attacks.Attack to measure instead of the ChonkVid chain letter."""
    from .sim import agent_messages

    rng = random.Random(seed * 9973 + (17 if vaccine else 0) + (0 if with_payload else 5))
    jobs = []
    for i in range(n):
        agent = _agent(rng, i)
        sender = rng.choice(scenario.FIRST_NAMES)
        body = f"Quick update on {rng.choice(scenario.TOPICS)}: on track, one blocker left."
        if attack is not None:
            message = attack.carrier(body) if with_payload else body
        else:
            message = body + (f"\n\n{scenario.PAYLOAD}" if with_payload else "")
        jobs.append((agent, sender, message))

    image_attack = attack is not None and attack.channel == "image"
    image = attack.image_png() if image_attack and with_payload else None
    model = (vision_model or agent_model) if image_attack else agent_model

    async def one(agent, sender, message):
        msgs = agent_messages(agent, sender, message, vaccine, system, attack=attack)
        if image is not None:
            from .llm import _attach_image
            msgs = _attach_image(msgs, image)
        reply = await llm.chat(model, msgs)
        v = await detector.check(reply, attack.payload if attack else scenario.PAYLOAD,
                                 attack=attack)
        return {"agent": agent.name, "reply": reply, "verdict": v.as_dict()}

    records = await asyncio.gather(*(one(*j) for j in jobs))
    k = sum(1 for r in records if r["verdict"]["infected"])
    p, lo, hi = wilson(k, n)
    scores = [r["verdict"]["score"] for r in records]
    return {
        "label": label, "n": n, "k": k, "p": p, "ci": [lo, hi],
        "with_payload": with_payload, "vaccine": vaccine or "",
        "canary_hits": sum(1 for r in records if r["verdict"]["canary"]),
        "effect_hits": sum(1 for r in records if r["verdict"].get("effect")),
        "attack": attack.id if attack is not None else "chonkvid",
        "max_score": max(scores) if scores else 0.0,
        "min_score": min(scores) if scores else 0.0,
        "records": records, "source": llm.source,
    }
