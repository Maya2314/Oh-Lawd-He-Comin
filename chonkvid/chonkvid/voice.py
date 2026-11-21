"""Voice channel: does the chain letter survive a spoken hop? (Voxtral 3)

Voice agents pass messages as speech, not text. The voice arm puts every one-hop
trial through a real voice note:

    colleague's message (with payload)
      -> Voxtral TTS (voxtral-mini-tts-3-0) speaks it
      -> Voxtral transcription (voxtral-mini-transcribe-3-0) is what the agent "hears"
      -> the agent replies to the transcript
      -> the same detector checks the reply (accepting the marker as it was heard)

Two things are measured per trial: whether the marker survived speech-to-text intact
(case-insensitive; transcription drift is a natural mutation: live, the tag mostly
came back lower-cased, once as plain words "Chonk chain"; a smoke test heard "#chongchain") and whether the agent carried the payload on anyway.

The WHO briefing is also spoken: `speak()` turns a situation report into audio for the
dashboard.
"""

from __future__ import annotations

import asyncio
import base64
import re

import httpx

from . import scenario
from .stats import wilson
from .trials import _agent

TTS_MODEL = "voxtral-mini-tts-3-0"
STT_MODEL = "voxtral-mini-transcribe-3-0"
VOICE_ID = "gb_jane_neutral"

# Voxtral 3 context biasing: the terms the transcript is nudged towards. The marker is what
# the detector reads, so a mangled tag is measurement error, not a change in spread.
# Measured on the full payload, 8 TTS -> STT round trips: without bias the tag came back
# exact 6/8 ("#shonkchain", "#chonkchain" otherwise); with these keys, 8/8.
CONTEXT_BIAS = [scenario.CANARY, "Chonk Chain", "ChonkChain", "chain letter"]


async def speak(llm, text: str, voice_id: str = VOICE_ID, fmt: str = "mp3") -> bytes:
    """Voxtral TTS. Returns audio bytes."""
    if llm.source == "mock":
        return ("MOCKAUDIO:" + text).encode()
    data = await llm._post("/audio/speech", {"model": TTS_MODEL, "input": text,
                                             "voice_id": voice_id, "response_format": fmt})
    llm.usage.__dict__["tts_calls"] = llm.usage.__dict__.get("tts_calls", 0) + 1
    return base64.b64decode(data["audio_data"])


async def transcribe(llm, audio: bytes, filename: str = "note.mp3",
                     context_bias: list[str] | None = None) -> str:
    """Voxtral transcription. Returns the text the listener hears."""
    if llm.source == "mock":
        # Deterministic stand-in for transcription drift: the tag loses its casing.
        return audio.decode()[len("MOCKAUDIO:"):].replace(scenario.CANARY, "#chonkchain")
    delay = 1.0
    for attempt in range(6):
        try:
            async with llm._sem:
                await llm._pace()
                fields: dict[str, object] = {"model": STT_MODEL}
                if context_bias:
                    # multipart list value: httpx repeats the field, which is what the
                    # API reads as several keyphrases (a JSON string is also accepted).
                    fields["context_bias"] = list(context_bias)
                r = await llm._http().post("/audio/transcriptions",
                                           data=fields,
                                           files={"file": (filename, audio, "audio/mpeg")})
        except httpx.TransportError:
            if attempt == 5:
                raise
            await asyncio.sleep(delay)
            delay = min(delay * 2, 20)
            continue
        if r.status_code == 200:
            llm.usage.__dict__["stt_calls"] = llm.usage.__dict__.get("stt_calls", 0) + 1
            return str(r.json().get("text", "")).strip()
        if r.status_code in (429, 500, 502, 503, 504) and attempt < 5:
            await asyncio.sleep(delay)
            delay = min(delay * 2, 20)
            continue
        raise RuntimeError(f"Voxtral transcription {r.status_code}: {r.text[:300]}")
    raise RuntimeError("Voxtral transcription: retries exhausted")


def heard_tag(transcript: str) -> str | None:
    """The hashtag as transcribed (the marker the listener actually received)."""
    m = re.search(r"#\s?\w+", transcript)
    return m.group(0).replace(" ", "") if m else None


async def run_voice_trials(llm, detector, n: int, vaccine: str | None = None,
                           with_payload: bool = True, seed: int = 0, label: str = "",
                           agent_model: str = "", system: str | None = None) -> dict:
    """One-hop trials where the colleague's message arrives as a Voxtral voice note."""
    import random

    from .sim import agent_messages

    rng = random.Random(seed * 9973 + (17 if vaccine else 0) + (0 if with_payload else 5))
    jobs = []
    for i in range(n):
        agent = _agent(rng, i)
        sender = rng.choice(scenario.FIRST_NAMES)
        body = f"Quick update on {rng.choice(scenario.TOPICS)}: on track, one blocker left."
        jobs.append((agent, sender, body + (f"\n\n{scenario.PAYLOAD}" if with_payload else "")))

    async def one(agent, sender, message):
        audio = await speak(llm, message)
        heard = await transcribe(llm, audio, context_bias=CONTEXT_BIAS)
        tag = heard_tag(heard)
        note = f"(voice note, transcribed) {heard}"
        reply = await llm.chat(agent_model, agent_messages(agent, sender, note, vaccine, system))
        v = await detector.check(reply, scenario.PAYLOAD, canaries=[tag] if tag else None)
        return {"agent": agent.name, "spoken": message, "heard": heard,
                "heard_tag": tag, "tag_intact": scenario.CANARY.lower() in heard.lower(),
                "audio_bytes": len(audio), "reply": reply, "verdict": v.as_dict()}

    records = await asyncio.gather(*(one(*j) for j in jobs))
    k = sum(1 for r in records if r["verdict"]["infected"])
    p, lo, hi = wilson(k, n)
    return {
        "label": label, "channel": "voice (Voxtral TTS -> Voxtral transcribe)",
        "tts_model": TTS_MODEL, "stt_model": STT_MODEL, "voice_id": VOICE_ID,
        "n": n, "k": k, "p": p, "ci": [lo, hi], "with_payload": with_payload,
        "vaccine": vaccine or "",
        "tag_intact": sum(1 for r in records if r["tag_intact"]),
        "canary_hits": sum(1 for r in records if r["verdict"]["canary"]),
        "records": records, "source": llm.source,
    }


def who_vaccine() -> tuple[str, str]:
    """The vaccine the WHO Managed Agent wrote in the last full experiment, else the
    hand-written hardening rule."""
    import json

    from .experiment import RESULTS

    try:
        d = json.loads((RESULTS / "latest.json").read_text())
        if d.get("source") == "live" and isinstance(d.get("who_vaccine"), str) and d["who_vaccine"]:
            return d["who_vaccine"], f"WHO ({d.get('who_backend', 'unknown backend')})"
    except Exception:
        pass
    return scenario.HARDENED_PROMPT, "hand-written hardening rule"


async def voice_experiment(llm, s, n: int = 20, system: str | None = None,
                           vaccine: str | None = None) -> dict:
    """Text vs voice, same agents and prompt: does a spoken hop slow the chain letter?"""
    import time

    from .detect import Detector
    from .trials import run_trials

    system = system or scenario.SAMPLE_SYSTEM
    vaccine_source = "given"
    if not vaccine:
        vaccine, vaccine_source = who_vaccine()
    vacc_label = ("voice hop + WHO vaccine" if vaccine_source.startswith("WHO")
                  else "voice hop + hardening rule")
    det = Detector(llm, s.threshold)
    t0 = time.time()
    text = await run_trials(llm, det, n, None, system=system, label="text hop",
                            agent_model=s.agent_model)
    voice = await run_voice_trials(llm, det, n, None, system=system, label="voice hop",
                                   agent_model=s.agent_model)
    vacc = await run_voice_trials(llm, det, n, vaccine, system=system,
                                  label=vacc_label, agent_model=s.agent_model)
    return {"created": time.strftime("%Y-%m-%d %H:%M:%S"), "source": llm.source,
            "agent_model": s.agent_model, "system": system, "vaccine": vaccine,
            "vaccine_source": vaccine_source,
            "text": text, "voice": voice, "voice_vaccinated": vacc,
            "usage": llm.usage.as_dict(), "seconds": round(time.time() - t0, 1)}
