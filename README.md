# ChonkVid — Oh Lawd He Comin'

**Flatten the curve of prompt injection.**

Le Chonk London (Mistral Vibe Hackathon, 21 Nov 2026). ChonkVid releases a harmless
self-spreading prompt into a population of Ministral agents and measures how fast it
spreads through *your* system prompt, then verifies a vaccine patch written by a Le
Chonk WHO agent with the same measurement.

## Quick start

All application code, measured results, and run instructions live in **`chonkvid/`**:

```bash
cd chonkvid
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r requirements.txt
cp .env.example .env   # optional MISTRAL_API_KEY for live mode; omit for MOCK rehearsal
.venv/bin/python -m chonkvid serve
```

Open **http://127.0.0.1:8000** for the epidemic dashboard and **/world** for the fleet
outbreak map.

## What's in this repo

| Path | Purpose |
|---|---|
| [chonkvid/](chonkvid/) | Python package, dashboard, world map, MCP/Vibe skill, Cloudflare worker |
| [demo/](demo/) | Recorded demo video (`chonkvid-demo.mp4`) |
| [supabase/](supabase/) | Shared DB migrations for recordings and reports |
| [docs/SUPABASE_BACKEND.md](docs/SUPABASE_BACKEND.md) | Backend wiring notes |

## Provenance

The runnable scaffold was prepared the night before the event and disclosed at
check-in. Measured numbers in `chonkvid/README.md` are from **live** runs on 21 Nov
2026 (Ministral 8B population, Le Chonk WHO via Mistral Managed Agents).

## Team

Built at Fora — Chancery House, London. Mistral Vibe was the build tool; stack is
Ministral, mistral-embed, Mistral Managed Agents (Le Chonk), and optional Voxtral for
voice-hop trials.