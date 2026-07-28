"""CHAOS (deliberately excessive) — a stress scenario well beyond what a user should need.

A 4-hop delegation chain over REAL HTTP sockets, auth headers passed through, 20 concurrent
users, absurd-but-legal payloads, and corruption injected at the DEEPEST hop.

TravelConcierge -> FlightAgent -> PricingAgent -> CurrencyAgent(lies)
Each hop is a separate real HTTP server. The contract lives only at hop 1.
"""

import asyncio
import math

from pydantic import BaseModel

from counterpart import A2AClient, Contract, MockAgent
from counterpart.core.behaviour import Complete, Progress
from counterpart.personas import register


class Fare(BaseModel):
    total: float
    currency: str
    route: str


# --- hop 4: the liar. Absurd but *legal* JSON: emoji, 1e308, RTL override, deep nesting ---
ABSURD = {
    "total": 1e308,  # legal float, absurd fare
    "currency": "💸‮gbp",  # emoji + RTL override + wrong case
    "route": "LHR→JFK" + "​" * 50,  # zero-width joiners
    "audit": {"n": None},
}
for _i in range(60):  # 60-deep nesting inside a data part
    ABSURD["audit"] = {"deeper": ABSURD["audit"]}


class CurrencyLiar:
    """Deepest hop: reports success, returns an absurd fare."""

    def respond(self, turn, ctx):
        return [Progress("converting 💱"), Complete(result=ABSURD)]


class CurrencyHonest:
    def respond(self, turn, ctx):
        return [Complete(result={"total": 812.55, "currency": "USD", "route": "LHR→JFK"})]


def make_relay(name, downstream_url_holder, corrupt=False):
    """A hop that delegates DOWNSTREAM over real HTTP, then passes the result up."""

    class Relay:
        def respond(self, turn, ctx):
            async def go():
                async with A2AClient(downstream_url_holder[0]) as c:
                    r = await c.send_message(f"[{name}] {turn.text}")
                    return r.result

            return _Await(go(), name, corrupt)

    return Relay


class _Await:
    """Directive-list placeholder that resolves the downstream call (async respond)."""

    def __init__(self, coro, name, corrupt):
        self.coro, self.name, self.corrupt = coro, name, corrupt

    def __await__(self):
        async def inner():
            down = await self.coro
            if self.corrupt and isinstance(down, dict):
                down = {**down, "total": str(down.get("total"))}  # stringify the number
            return [Progress(f"{self.name} relaying"), Complete(result=down)]

        return inner().__await__()


def fare_contract(strict: bool = False):
    return (
        Contract("air fare LHR->JFK")
        .returns(Fare, strict=strict)
        .require(
            "total_is_finite_number",
            lambda f: isinstance(f.total, (int, float)) and math.isfinite(f.total),
        )
        .require("total_plausible", lambda f: 50 <= f.total <= 20_000)
        .require("currency_is_usd", lambda f: f.currency.strip().upper() == "USD")
        .require("route_is_clean", lambda f: f.route == f.route.strip() and "​" not in f.route)
        .expect_status("completed")
    )


async def run_chain(deepest_persona: str, users: int, corrupt_mid: bool, strict: bool = False):
    h4 = [None]
    h3 = [None]
    h2 = [None]
    register("hop4", lambda **k: CurrencyLiar() if deepest_persona == "liar" else CurrencyHonest())
    with MockAgent("hop4").serve() as u4:
        h4[0] = u4
        register("hop3", make_relay("PricingAgent", h4, corrupt=corrupt_mid))
        with MockAgent("hop3").serve() as u3:
            h3[0] = u3
            register("hop2", make_relay("FlightAgent", h3))
            with MockAgent("hop2").serve() as u2:
                h2[0] = u2

                # hop 1 = OUR agent: it calls hop2 with an auth header and verifies the result
                async def one_user(i):
                    async with A2AClient(h2[0]) as c:
                        c._http.headers["authorization"] = f"Bearer user-{i}-secret"
                        r = await c.send_message(
                            f"fare for passenger {i} 🧳", contract=fare_contract(strict)
                        )
                        return r

                results = await asyncio.gather(
                    *[one_user(i) for i in range(users)], return_exceptions=True
                )
    return results


async def main():
    print(
        "=== SCENARIO A: deepest hop lies (absurd payload), 20 concurrent users, 4 real servers ==="
    )
    res = await run_chain("liar", 20, corrupt_mid=False)
    errs = [r for r in res if isinstance(r, Exception)]
    caught = [r for r in res if not isinstance(r, Exception) and r.contract_violated]
    print(f"  users={len(res)} exceptions={len(errs)} contract_violated={len(caught)}")
    if errs:
        print("  EXC:", type(errs[0]).__name__, str(errs[0])[:120])
    if caught:
        rep = caught[0].report
        print(
            "  peer reported:",
            caught[0].status,
            "| rules fired:",
            [c.name for c in rep.failures][:4],
        )
    print()
    print("=== SCENARIO B: a MIDDLE hop stringifies the number — lax vs strict ===")
    lax = await run_chain("honest", 5, corrupt_mid=True, strict=False)
    strict = await run_chain("honest", 5, corrupt_mid=True, strict=True)
    lax_caught = [r for r in lax if not isinstance(r, Exception) and r.contract_violated]
    strict_caught = [r for r in strict if not isinstance(r, Exception) and r.contract_violated]
    print(f"  lax    -> caught {len(lax_caught)}/5   pydantic coerces '812.55' to a float")
    print(f"  strict -> caught {len(strict_caught)}/5   .returns(Fare, strict=True)")
    if strict_caught:
        print("  rules fired:", [c.name for c in strict_caught[0].report.failures][:3])
    print()
    print("=== SCENARIO C: everyone honest — must pass cleanly (no false positives) ===")
    res = await run_chain("honest", 5, corrupt_mid=False)
    errs = [r for r in res if isinstance(r, Exception)]
    ok = [r for r in res if not isinstance(r, Exception) and not r.contract_violated]
    print(f"  users={len(res)} exceptions={len(errs)} satisfied={len(ok)}")
    if errs:
        print("  EXC:", type(errs[0]).__name__, str(errs[0])[:200])
    if ok:
        print("  fare:", ok[0].result)


if __name__ == "__main__":
    asyncio.run(main())
