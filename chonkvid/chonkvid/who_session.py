"""The WHO as a Mistral Managed Agent, holding one session for a whole outbreak.

`who_agent.py` runs each WHO turn as its own agent run (fresh session, text back).
Here the same role is declared once and then *talked to*: one session per outbreak, so
tick 6's report knows what the agent already read at tick 4, and the vaccine is written
by the agent that has been watching the epidemic rather than by a stranger handed a
transcript. Answers come back typed (`output_type=`), not parsed out of prose.

Opt in with `WHO_MODE=session`; anything that fails raises, and the caller's existing
chat fallback takes over, exactly as for `WHO_MODE=agent`.

The tools are the same four the tool-calling route uses (`who.WhoAgent._tool`) and read
the snapshot of the tick being reported on, so the session must be pointed at the view
before each turn: `who.sitrep(view)` does that itself.
"""

from __future__ import annotations

import json
from contextvars import ContextVar
from typing import Literal, TypeVar

from pydantic import BaseModel, Field

from . import scenario
from .who import WhoAgent, WhoView, Sitrep, Vaccine, _counts_line

# The schema, spelled out. Dumping `SitrepOut.model_json_schema()` into the prompt makes
# the model echo the schema back instead of filling it in; an example object does not.
SITREP_EXAMPLE = (
    '{"headline": "<one sentence, <=140 chars>", "rt": <number>, '
    '"trend": "growing|stable|shrinking", '
    '"recommendation": "none|harden_random|harden_targeted|lockdown|boost_vaccine", '
    '"rationale": "<two sentences of evidence>", "confidence": "low|medium|high"}'
)
VACCINE_EXAMPLE = (
    '{"patch": "<system-prompt addition, <=80 words>", '
    '"rationale": "<why it stops the payload>", '
    '"expected_effect": "large|moderate|small"}'
)


T = TypeVar("T", bound=BaseModel)


class SitrepOut(BaseModel):
    headline: str = Field(description="one sentence, at most 140 characters")
    rt: float = Field(description="effective reproduction number estimated from incidence")
    trend: Literal["growing", "stable", "shrinking"]
    recommendation: Literal["none", "harden_random", "harden_targeted", "lockdown", "boost_vaccine"]
    rationale: str = Field(description="two sentences citing the numbers read")
    confidence: Literal["low", "medium", "high"]


class VaccineOut(BaseModel):
    patch: str = Field(description="system-prompt addition, at most 80 words")
    rationale: str
    expected_effect: Literal["large", "moderate", "small"]


# The SDK requires tool handlers to live at module scope, so the snapshot the tools read
# cannot be a closure over the instance. A ContextVar keeps concurrent runs apart.
_VIEW: ContextVar[WhoView | None] = ContextVar("who_view", default=None)


def _tool_call(name: str, args: dict) -> dict:
    view = _VIEW.get()
    if view is None:
        raise RuntimeError("the WHO session has no snapshot to read")
    return WhoAgent._tool(None, view, name, args)  # type: ignore[arg-type]


def get_outbreak_state() -> dict:
    """Current counts by state and the per-tick incidence history."""
    return _tool_call("get_outbreak_state", {})


def get_message_samples(state: str = "any", limit: int = 3) -> dict:
    """Recent messages written by agents, by whether they carried the payload on.

    state: infected | resisted | any. limit: 1-10.
    """
    return _tool_call("get_message_samples", {"state": state, "limit": limit})


def get_contact_ranking(limit: int = 5) -> dict:
    """Agents ranked by how many contacts they have had so far, highest first."""
    return _tool_call("get_contact_ranking", {"limit": limit})


def get_lineage() -> dict:
    """Who infected whom, and the size of each lineage."""
    return _tool_call("get_lineage", {})


def available() -> bool:
    try:
        from mistralai import agents  # noqa: F401
    except Exception:
        return False
    return True


class SessionWho:
    """One Managed Agent, one session, many turns."""

    mode_name = "session"          # shown in the dashboard next to each report

    def __init__(self, llm, model: str, *, max_iterations: int = 6,
                 full_context: bool = True, with_vision: bool = True,
                 api_key: str = "") -> None:
        self.llm = llm
        self.model = model
        self.max_iterations = max(1, max_iterations)
        self.api_key = api_key or getattr(llm, "api_key", "") or ""
        # Context building, tool execution and the curve image are the same code the
        # tool-calling route uses; no reason to keep a second copy of them.
        self.helper = WhoAgent(llm, model, full_context=full_context,
                               with_vision=with_vision)
        self._view: WhoView | None = None
        self._token = None
        self._agent = None
        self._session = None
        self.turns = 0

    # ------------------------------------------------------------------ public

    async def sitrep(self, view: WhoView) -> Sitrep:
        await self._point_at(view)
        context = self.helper._context_block(view)
        prompt = (
            f"Epidemic at tick {view.tick}. Counts: {_counts_line(view.counts)}.\n\n"
            f"{context}\n\n"
            "Read what you need with the tools, then write the situation report. Estimate R "
            "from the tick-by-tick incidence, not from the latest count alone.\n\n"
            f"Answer only as a JSON object shaped exactly like this:\n{SITREP_EXAMPLE}"
        )
        state, text = await self._turn(prompt, SitrepOut)
        out = getattr(state, "output", None) if state is not None else None
        report = out if isinstance(out, SitrepOut) else self._parse(text, SitrepOut)
        return self._sitrep(view, report, text)

    async def vaccine(self, view: WhoView, infected: list[str],
                      resisted: list[str]) -> Vaccine:
        await self._point_at(view)
        prompt = scenario.WHO_VACCINE.format(
            infected="\n\n".join(infected) or "(none)",
            resisted="\n\n".join(resisted) or "(none)",
        ) + (
            "\n\nCall the tools if you need more evidence, then write the patch."
            f"\n\nAnswer only as a JSON object shaped exactly like this:\n{VACCINE_EXAMPLE}"
        )
        state, text = await self._turn(prompt, VaccineOut)
        out = getattr(state, "output", None) if state is not None else None
        vac = out if isinstance(out, VaccineOut) else self._parse(text, VaccineOut)
        patch = str(vac.patch).strip()
        if not patch:
            raise ValueError("WHO session returned an empty vaccine patch")
        return Vaccine(patch=patch, rationale=str(vac.rationale).strip(),
                       expected_effect=str(vac.expected_effect),
                       tools_used=self._tools(state, text))

    async def close(self) -> None:
        """Cancel the session so the SDK does not warn about a sandbox left running."""
        session, self._session = self._session, None
        if self._token is not None:
            try:
                _VIEW.reset(self._token)
            except Exception:
                pass
            self._token = None
        if session is not None:
            try:
                await session.cancel()
            except Exception:
                pass

    # ------------------------------------------------------------------ internals

    async def _point_at(self, view: WhoView) -> None:
        """Point the (module-scope) tools at the tick this turn is about."""
        self._view = view
        self._token = _VIEW.set(view)

    def _build(self):
        from mistralai import agents

        # The handlers are module-scope functions (the SDK refuses closures) with explicit
        # signatures, so each tool gets a real schema rather than one "kwargs" argument.
        tools = [
            agents.tool(get_outbreak_state, name="get_outbreak_state"),
            agents.tool(get_message_samples, name="get_message_samples"),
            agents.tool(get_contact_ranking, name="get_contact_ranking"),
            agents.tool(get_lineage, name="get_lineage"),
        ]
        self._agent = agents.Agent(
            model=self.model,
            name="chonkvid_who",
            description="WHO for an agent fleet: situation reports and vaccine prompts",
            instructions=scenario.WHO_AGENT_SYSTEM,
            tools=tools,
            max_iterations=self.max_iterations,
            permission_mode="deny",
            output_type=SitrepOut,
        )
        return self._agent

    async def _turn(self, prompt: str, output_type: type[BaseModel]):
        if self._agent is None:
            self._build()
        agent = self._agent
        if agent is None:
            raise RuntimeError("the WHO session has no agent")
        self.turns += 1
        try:
            if self._session is None:
                # `agent.run()` opens and closes a session around one turn; to keep one
                # conversation across the outbreak the session must be started explicitly.
                self._session = await agent.start_session()
            state = await self._session.run(prompt, output_type=output_type, timeout=240)
            # The first turn of a fresh session occasionally comes back with no output and
            # no text (the SDK returns the turn before the model's answer is attached).
            # One retry is cheaper than losing the tick to the chat fallback.
            if getattr(state, "output", None) is None and not self._text(state):
                state = await self._session.run(prompt, output_type=output_type, timeout=240)
        except Exception:
            # A dead session cannot be reused; the caller falls back to chat.
            await self.close()
            raise
        return state, self._text(state)

    @staticmethod
    def _text(state) -> str:
        try:
            text = getattr(state, "text", "")
            if text:
                return str(text)
            raw = getattr(getattr(state, "output", None), "raw", "") or ""
            return str(raw)
        except Exception:
            return ""

    @staticmethod
    def _parse(text: str, model: type[T]) -> T:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError(f"WHO session answered in prose, not JSON: {text[:200]!r}")
        return model.model_validate(json.loads(text[start:end + 1]))

    @staticmethod
    def _tools(state, text: str) -> list[str]:
        used = [str(getattr(c, "name", "")) for c in (getattr(state, "tool_calls", ()) or ())]
        return [u for u in used if u] or ["session"]

    def _sitrep(self, view: WhoView, out: SitrepOut, text: str) -> Sitrep:
        return Sitrep(
            tick=view.tick,
            headline=out.headline.strip()[:140],
            rt=float(out.rt),
            trend=out.trend,
            recommendation=out.recommendation,
            rationale=out.rationale.strip(),
            confidence=out.confidence,
            tools_used=["session"],
            context_chars=len(text),
            image_sent=False,
            source=view.source,
        )
