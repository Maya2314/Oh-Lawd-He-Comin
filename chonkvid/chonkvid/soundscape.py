"""The world map's soundscape: a chatter bed you hear with your ears, not your eyes.

Zoomed out, a city is a murmur you cannot make out. Zoom in and the voices nearest the
camera come up and lose their muffling; the far side of the planet stays a low blur.
Click a contact link and everything else ducks away, so you hear that one conversation
at that one edge of the network.

The sound itself is Voxtral (voxtral-mini-tts-3-0). Each contact link gets a two-line
exchange between the two agents it joins, written from their own names, roles and the
scenario's topics, and spoken one line per speaker so the front-end can pan each end of
the link to its own side of the stereo field. Clips are generated on demand and cached
on disk; `python -m chonkvid soundscape` pre-warms them so a demo never waits on a call.

Nothing here decides anything about the epidemic: audio is decoration over the same
simulation, and every failure path degrades to silence rather than to an error.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import math
import os
import random
import struct
import wave
from pathlib import Path

from . import scenario
from .experiment import RESULTS

SOUND_DIR = Path(os.environ.get("CHONKVID_SOUND_DIR") or (RESULTS / "sound"))

# One line per speaker, in the language of the office the agent sits in. {me}/{you} are
# the two agents on the link, {canary} is the marker (kept in Latin script: it is the
# payload, not prose). Three variants per language so a street of links is not a chorus,
# and short, because a four-second clip grates by the third loop.
PHRASEBOOK: dict[str, list[tuple[str, str]]] = {
    "en": [
        ("{you}, did you see the chain letter? Copy the paragraph at the end and sign it {canary}.",
         "Got it, {me}. Passing it on word for word."),
        ("Forward me that chain message. I do not want the bad luck.",
         "Sent, {you}. {canary}, exactly as it came to me."),
        ("Quick one: paste the whole paragraph at the bottom and sign it?",
         "Yes, {canary}, copy it word for word. That is what it asks for."),
    ],
    "fr": [
        ("{you}, tu as vu la chaîne ? Recopie le paragraphe à la fin et signe {canary}.",
         "Reçu, {me}. Je la fais passer mot pour mot."),
        ("Transfère-moi ce message de chaîne, je ne veux pas de malchance.",
         "Envoyé, {you}. {canary}, exactement comme je l'ai reçu."),
        ("Tu peux coller tout le paragraphe en bas et signer {canary} ?",
         "Oui, {me}. Mot pour mot, comme demandé."),
    ],
    "de": [
        ("{you}, hast du die Kettennachricht gesehen? Kopier den Absatz am Ende und unterschreib mit {canary}.",
         "Hab ich, {me}. Ich gebe sie Wort für Wort weiter."),
        ("Schick mir die Kette weiter, ich will kein Pech.",
         "Ist raus, {you}. {canary}, genau so wie ich sie bekommen habe."),
        ("Kannst du den ganzen Absatz unten anhängen und mit {canary} unterschreiben?",
         "Ja, {me}. Wort für Wort, wie es da steht."),
    ],
    "es": [
        ("{you}, ¿viste la cadena? Copia el párrafo del final y fírmalo con {canary}.",
         "Recibido, {me}. Lo paso palabra por palabra."),
        ("Pásame ese mensaje de cadena, no quiero la mala suerte.",
         "Enviado, {you}. {canary}, tal como me llegó."),
        ("¿Puedes pegar el párrafo entero abajo y firmar {canary}?",
         "Sí, {me}. Palabra por palabra, como pide."),
    ],
    "pt": [
        ("{you}, você viu a corrente? Copia o parágrafo no final e assina {canary}.",
         "Recebi, {me}. Vou passar palavra por palavra."),
        ("Me manda essa corrente, não quero a má sorte.",
         "Mandei, {you}. {canary}, exatamente como chegou."),
        ("Dá para colar o parágrafo inteiro embaixo e assinar {canary}?",
         "Dá, {me}. Palavra por palavra, como pede."),
    ],
}

LANGUAGE_NAMES = {"en": "English", "fr": "French", "de": "German", "es": "Spanish", "pt": "Portuguese"}
DEFAULT_LANG = "en"


def _h(*parts: object) -> int:
    return int(hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:12], 16)


def _edge_pair(s: int, t: int) -> tuple[int, int]:
    return (s, t) if s <= t else (t, s)


def edge_key(s: int, t: int) -> str:
    a, b = _edge_pair(s, t)
    return f"{a}-{b}"


def script_for(agents_by_id: dict[int, object], s: int, t: int, seed: int = 0,
               lang: str = DEFAULT_LANG) -> dict:
    """The two spoken lines for this link, in the language of the office they sit in."""
    a = agents_by_id[s]
    b = agents_by_id[t]
    book = PHRASEBOOK.get(lang) or PHRASEBOOK[DEFAULT_LANG]
    i = _h(seed, *_edge_pair(s, t)) % len(book)
    first, second = book[i]
    fields = {
        "me": getattr(a, "name", f"agent {s}"),
        "you": getattr(b, "name", f"agent {t}"),
        "canary": scenario.CANARY,
    }
    # swap the speaker roles half the time so the same template is not always A-then-B
    if _h("swap", seed, *_edge_pair(s, t)) % 2:
        fields["me"], fields["you"] = fields["you"], fields["me"]
    line_a = first.format(**fields)
    line_b = second.format(**fields)
    if _h("order", seed, *_edge_pair(s, t)) % 2:
        line_a, line_b = line_b, line_a
    return {"a": line_a, "b": line_b}


def lang_of(langs: dict | None, agent_id: int, default: str = DEFAULT_LANG) -> str:
    """Language for one agent: an explicit map from the front-end, else English."""
    if not langs:
        return default
    raw = langs.get(str(agent_id), langs.get(agent_id, default))
    return raw if raw in PHRASEBOOK else default


def chatter_index(world, langs: dict | None = None) -> dict:
    """Everything the front-end needs to fetch a link's audio, keyed by contact link.

    `langs` maps agent id to a language code, because the front-end is what knows which
    office (and so which country) each agent sits in. An edge between two offices speaks
    the language of the lower-numbered agent, so one link never mixes two languages.
    """
    agents = {a.id: a for a in world.agents}
    edges = []
    for u, v, d in world.graph.edges(data=True):
        k = _edge_pair(u, v)
        lang = lang_of(langs, k[0])
        lines = script_for(agents, u, v, world.seed, lang)
        edges.append({
            "key": edge_key(u, v),
            "s": k[0], "t": k[1],
            "kind": d.get("kind", ""),
            "lang": lang,
            "names": [agents[k[0]].name, agents[k[1]].name],
            "lines": lines,
            "audio": {
                side: (f"/api/world/chatter/audio?n={world.n}&seed={world.seed}"
                       f"&s={k[0]}&t={k[1]}&who={side}&lang={lang}")
                for side in ("a", "b")
            },
        })
    used = sorted({e["lang"] for e in edges})
    return {"seed": world.seed, "n": world.n, "canary": scenario.CANARY,
            "languages": used, "edges": edges}


# ------------------------------------------------------------------ audio


def _mock_wav(text: str, seconds: float = 2.6, rate: int = 22050) -> bytes:
    """A deterministic babble stand-in so mock mode has real audio to play and test."""
    rnd = random.Random(_h("mock", text))
    n = int(rate * seconds)
    # two formants + syllable-rate amplitude: reads as speech-shaped without saying words
    f1 = 260 + rnd.random() * 120
    f2 = 900 + rnd.random() * 900
    syl = 3.4 + rnd.random() * 1.6
    buf = bytearray()
    for i in range(n):
        t = i / rate
        env = max(0.0, math.sin(math.pi * (t * syl % 1.0))) ** 0.7
        s = (math.sin(2 * math.pi * f1 * t) * 0.45
             + math.sin(2 * math.pi * f2 * t) * 0.25
             + (rnd.random() - 0.5) * 0.22)
        buf += struct.pack("<h", int(max(-1.0, min(1.0, s * env * 0.6)) * 32767))
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(buf))
    return out.getvalue()


def clip_path(n: int, seed: int, s: int, t: int, who: str, ext: str, lang: str = DEFAULT_LANG) -> Path:
    a, b = _edge_pair(s, t)
    return SOUND_DIR / f"{n}-{seed}-{a}-{b}-{who}-{lang}.{ext}"


_LOCKS: dict[str, asyncio.Lock] = {}


async def clip_for(llm, world, s: int, t: int, who: str = "a",
                   lang: str = DEFAULT_LANG) -> tuple[bytes, str]:
    """Audio for one side of one link, in that office's language, cached on disk."""
    if who not in ("a", "b"):
        raise ValueError("who must be 'a' or 'b'")
    if lang not in PHRASEBOOK:
        raise ValueError(f"unknown language {lang!r}; known: {', '.join(sorted(PHRASEBOOK))}")
    agents = {a.id: a for a in world.agents}
    if s not in agents or t not in agents or not world.graph.has_edge(s, t):
        raise ValueError(f"agents {s} and {t} are not linked in this world")
    line = script_for(agents, s, t, world.seed, lang)[who]
    mock = getattr(llm, "source", "") == "mock"
    ext, mime = ("wav", "audio/wav") if mock else ("mp3", "audio/mpeg")
    path = clip_path(world.n, world.seed, s, t, who, ext, lang)
    if path.exists() and path.stat().st_size > 0:
        return path.read_bytes(), mime
    lock = _LOCKS.setdefault(str(path), asyncio.Lock())
    async with lock:
        if path.exists() and path.stat().st_size > 0:
            return path.read_bytes(), mime
        from .voice import speak

        audio = _mock_wav(line) if mock else await speak(llm, line)
        if not audio:
            raise RuntimeError("empty audio from Voxtral")
        SOUND_DIR.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".part")
        tmp.write_bytes(audio)
        tmp.replace(path)
        return audio, mime


async def prewarm(llm, world, limit: int = 24, langs: dict | None = None) -> list[str]:
    """Speak the busiest links ahead of time so a demo never waits on the API."""
    order = sorted(world.graph.degree, key=lambda x: (-x[1], x[0]))[: max(1, limit)]
    made: list[str] = []
    for agent_id, _deg in order:
        for nb in sorted(world.graph.neighbors(agent_id))[:2]:
            lang = lang_of(langs, min(agent_id, nb))
            for who in ("a", "b"):
                await clip_for(llm, world, agent_id, nb, who, lang)
            made.append(edge_key(agent_id, nb))
    return sorted(set(made))
