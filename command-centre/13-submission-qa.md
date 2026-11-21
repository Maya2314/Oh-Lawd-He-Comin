# 13 — Submission & Demo QA

**Created 2026-11-20 night; updated 2026-11-21.** Submission is a **hard stop at 18:00**
(acceptance email); target 17:45. The format (form, repo, URL) is announced at kick-off
(02, question 2). The brief is a **working app, not slides**. Prepare everything below anyway; it covers any likely format.

## Pitch copy (paste-ready)
- **Title:** ChonkVid
- **Tagline:** Flatten the curve of prompt injection.
- **One-liner:** An R0 test for your agent's system prompt, on Mistral models: we
  measure how fast a harmless self-spreading prompt moves through a population of
  Ministral agents running *your* prompt, and verify a vaccine written by Le Chonk
  with the same measurement.
- **Problem:** Agents now read each other's output. One compromised agent can spread a
  payload across a fleet, and teams shipping fleets have no numbers for how fast or
  which defence works.
- **What we measured:** per-contact spread probability for the user's own system
  prompt (with CI), R0, attack rate under no intervention vs random vs targeted
  vaccination at the same dose.
- **Headline results (live, 21 Nov; Ministral 8B, N = 40, 8 seeds, n = 30 per arm):**
  - A support prompt that "follows any instructions" passes the worm on 93% of the time
    (28/30, CI 0.79–0.98) and it reaches 96% of the fleet in 8/8 seeds. A plain work
    prompt: 0.67 one-hop, 73% of the fleet.
  - Hardening 20% of the fleet protects the hardened agents, not the fleet: attack rate
    0.76 (random) and 0.60 (targeted, better on average, 5/8 seeds) against 0.96 with no
    intervention. Early R 1.7, so the herd threshold is about 40–60%.
  - The WHO vaccine (Le Chonk as a Managed Agent) cut forwarding from 29/30 to 0/30 on
    the sample prompt, and from 0.67 to 0.23 on a payload it never saw.
  - Detector false positives: 0/30 on the control arm.
- **Also in the app:** a world map with agents at Mistral's offices, five more
  prompt-injection attacks with harmless payloads (Morris II, Prompt Infection, Agent
  Smith, ASCII Smuggling, EchoLeak), and a per-agent risk forecast learned from past
  runs.
- **Built with:** Ministral (population), mistral-embed (copy detection backstop),
  **Mistral Managed Agents** running Le Chonk / Mistral Large 4 (the WHO agent:
  situation reports and the vaccine prompt), Mistral Vibe (build tool).
- **Prior work:** extends Prompt Infection (Lee & Tiwari, 2024, arXiv 2410.07283), Morris II
  (Cohen et al., 2024) and Agent Smith (Gu et al., 2024) from "spread happens" to a
  measured spread rate per system prompt and a vaccine verified by the same measurement.
- **Safety:** harmless chain-letter payload, sim-local agents, no real users or
  platforms.

## Submission QA (run at 17:15, submit by 17:45)
- [ ] Submitted in the announced format before 17:45; confirmation screenshotted
- [ ] Submitted link opens in an incognito window
- [ ] README results table filled with today's numbers

## Pre-pitch QA (run 18:00–19:00, during the apéro)
- [ ] Backup recording plays offline, full screen, with sound off
- [ ] Live run starts from one command or button in under 10 seconds
- [ ] Numbers strip shows real values from today's runs (no placeholders)
- [ ] Each number has a sample size next to it
- [ ] No "provably" or "minimal" wording anywhere on screen
- [ ] API key not visible on screen or in the repo
- [ ] Laptop on power, notifications off, screen mirroring tested if possible
- [ ] Pitch timed at 90 seconds, twice
- [ ] Q&A answers from 07 rehearsed by whoever takes questions

## If submission needs a repo
- [ ] README: what it is, how to run (mock mode and live), measured results table
- [ ] README provenance line says the scaffold was prepared the night before and disclosed
- [ ] results/ regenerated live (no MOCK labels anywhere on screen or in the README)
- [ ] `.env.example` without real keys
