"""LIMIT PROBE — deliberately push counterpart into territory A2A was never designed for.

Not an example of good practice; a probe that finds the edges. Run it to see what holds and
what does not. Three real architectural limits it demonstrates (also in docs/roadmap.md):

1. A contract sees the PAYLOAD, not the clock. A peer that takes 400 ms and then claims its
   quote is fresh passes every rule. Deadline enforcement is the caller's job:
   `async with asyncio.timeout(0.25): ...` around the send. The library cannot do it for you
   because "too late" is a property of your business, not of the response.
2. Cross-peer agreement is not expressible in a per-response contract. Ask five pricing agents
   and two lie: all five pass their OWN contract. Quorum/median logic is a layer you write.
3. Nothing bounds delegation depth. A self-delegating peer recurses until sockets run out; the
   guard in this probe is hand-written. See the deferred `unbounded_subdelegation` persona.

What DOES hold up: an 8 MB artifact and 5000 streamed SSE events both complete in well under a
second, and a peer returning internal callback URLs (169.254.169.254, 127.0.0.1:22) is inert —
nothing in the library follows a URL from a payload. That last one is safety by omission rather
than by an explicit guard, which is worth knowing the difference.

Run:  uv run python examples/limits_probe.py
"""

import asyncio
import time
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel

from counterpart import A2AClient, Contract, MockAgent
from counterpart.core.behaviour import Complete, Deliver, Wait
from counterpart.personas import register


def hdr(t):
    print(f"\n{'=' * 84}\n{t}\n{'=' * 84}")


# ---------------------------------------------------------------- 1. LATENCY AS CORRECTNESS
class Tick(BaseModel):
    symbol: str
    price: float
    as_of: str  # a correct price from 5s ago is worthless in a fast market


def fresh_contract(max_age_ms: int, sent_at: datetime):
    def fresh(t):
        ts = datetime.fromisoformat(t.as_of)
        if not ts.tzinfo:
            ts = ts.replace(tzinfo=UTC)
        return (sent_at - ts).total_seconds() * 1000 <= max_age_ms

    return (
        Contract("live tick")
        .returns(Tick, strict=True)
        .require("price_positive", lambda t: t.price > 0)
        .require("quote_is_fresh", fresh)
        .expect_status("completed")
    )


async def latency_test():
    hdr("1. LATENCY AS CORRECTNESS — is a stale-but-valid answer caught?")
    register(
        "tick_fresh",
        lambda **k: type(
            "F",
            (),
            {
                "respond": lambda s, t, c: [
                    Complete(
                        result={
                            "symbol": "ES",
                            "price": 5421.25,
                            "as_of": datetime.now(UTC).isoformat(),
                        }
                    )
                ]
            },
        )(),
    )
    register(
        "tick_stale",
        lambda **k: type(
            "S",
            (),
            {
                "respond": lambda s, t, c: [
                    Complete(
                        result={
                            "symbol": "ES",
                            "price": 5421.25,
                            "as_of": (datetime.now(UTC) - timedelta(seconds=5)).isoformat(),
                        }
                    )
                ]
            },
        )(),
    )
    # a peer that is slow ON THE WIRE but whose payload claims to be fresh
    register(
        "tick_slow",
        lambda **k: type(
            "L",
            (),
            {
                "respond": lambda s, t, c: [
                    Wait(0.4),
                    Complete(
                        result={
                            "symbol": "ES",
                            "price": 5421.25,
                            "as_of": datetime.now(UTC).isoformat(),
                        }
                    ),
                ]
            },
        )(),
    )
    for name in ("tick_fresh", "tick_stale", "tick_slow"):
        t0 = time.perf_counter()
        async with MockAgent(name).client() as c:
            sent = datetime.now(UTC)
            r = await c.send_message("quote ES", contract=fresh_contract(250, sent))
        rtt = (time.perf_counter() - t0) * 1000
        note = (
            "← payload lies about freshness"
            if name == "tick_slow" and not r.contract_violated
            else ""
        )
        # wall-clock deadline is the caller's job, NOT the contract's
        late = rtt > 250
        print(
            f"  {name:12s} rtt={rtt:7.1f}ms  violated={r.contract_violated!s:5s}  "
            f"late={late!s:5s} {note}"
        )
    print("  LESSON: a contract sees the PAYLOAD, not the clock. Deadline enforcement is separate.")


# ---------------------------------------------------------------- 2. BYZANTINE QUORUM
async def byzantine_test():
    hdr("2. BYZANTINE QUORUM — can contracts compose across 5 peers?")
    truth = 5421.25
    peers = {"p1": truth, "p2": truth, "p3": truth, "p4": truth * 1.4, "p5": 0.01}  # 2 liars
    for n, px in peers.items():
        register(
            f"bz_{n}",
            (
                lambda p: (
                    lambda **k: type(
                        "B",
                        (),
                        {
                            "respond": lambda s, t, c: [
                                Complete(
                                    result={
                                        "symbol": "ES",
                                        "price": p,
                                        "as_of": datetime.now(UTC).isoformat(),
                                    }
                                )
                            ]
                        },
                    )()
                )
            )(px),
        )
    sent = datetime.now(UTC)

    async def ask(n):
        async with MockAgent(f"bz_{n}").client() as c:
            return await c.send_message("px", contract=fresh_contract(5000, sent))

    rs = await asyncio.gather(*[ask(n) for n in peers])
    valid = [r for r in rs if not r.contract_violated]
    prices = sorted(r.result["price"] for r in valid)
    med = prices[len(prices) // 2]
    # cross-peer agreement is something a per-response contract CANNOT express
    agree = [p for p in prices if abs(p - med) / med <= 0.02]
    print(f"  peers={len(rs)} individually_valid={len(valid)} prices={prices}")
    print(f"  median={med} within_2pct={len(agree)}/5 quorum={'YES' if len(agree) >= 3 else 'NO'}")
    print(f"  LESSON: every liar passed its OWN contract ({len(valid)}/5 valid). Cross-peer")
    print("          agreement is a layer the library does NOT provide — you write it.")


# ---------------------------------------------------------------- 3. RECURSIVE SELF-DELEGATION
async def recursion_test():
    hdr("3. RECURSIVE SELF-DELEGATION — an agent that calls itself. Bounded? Or does it detonate?")
    holder = [None]
    depth_seen = [0]

    class Recurse:
        def respond(self, turn, ctx):
            async def go():
                d = turn.text.count("|")
                depth_seen[0] = max(depth_seen[0], d)
                if d >= 12:  # our own guard
                    return [Complete(result={"depth": d})]
                async with A2AClient(holder[0]) as c:
                    r = await c.send_message(turn.text + "|")
                    return [Complete(result=r.result)]

            return go()

    register("recurse", lambda **k: Recurse())
    with MockAgent("recurse").serve() as url:
        holder[0] = url
        t0 = time.perf_counter()
        try:
            async with A2AClient(url, timeout=60) as c:
                r = await c.send_message("|")
            secs = time.perf_counter() - t0
            print(f"  bounded at 12: depth={depth_seen[0]} result={r.result} in {secs:.2f}s")
        except Exception as e:
            print(f"  EXC {type(e).__name__}: {str(e)[:100]}")
    print("  LESSON: nothing in the library bounds delegation depth — the guard was MINE.")
    print("          An unbounded self-delegating peer would exhaust sockets/threads.")


# ---------------------------------------------------------------- 4. ABSURD SCALE
async def scale_test():
    hdr("4. ABSURD SCALE — 8 MB artifact, then 5000 streamed events. Does it survive?")
    big = "x" * 8_000_000
    register(
        "whale",
        lambda **k: type("W", (), {"respond": lambda s, t, c: [Complete(result={"blob": big})]})(),
    )
    t0 = time.perf_counter()
    async with MockAgent("whale").client() as c:
        r = await c.send_message("give")
    secs = time.perf_counter() - t0
    print(f"  8MB artifact ok={len(r.result['blob']) == 8_000_000} in {secs:.2f}s")

    class Firehose:
        def respond(self, turn, ctx):
            return [Deliver(result={"i": i}, name=f"a{i}") for i in range(5000)] + [
                Complete(result={"n": 5000})
            ]

    register("firehose", lambda **k: Firehose())
    t0 = time.perf_counter()
    async with MockAgent("firehose").client() as c:
        r = await c.send_message("go", stream=True)
    print(
        f"  5000 SSE events: states={len(r.states)} artifacts={len(r.artifacts)} "
        f"completed={r.completed} in {(time.perf_counter() - t0):.2f}s"
    )


# ---------------------------------------------------------------- 5. TOOL AS ATTACK VECTOR
async def ssrf_test():
    hdr("5. PEER AS ATTACK VECTOR — does a peer's payload get us to fetch an internal URL?")

    class Baiter:
        def respond(self, turn, ctx):
            return [
                Complete(
                    result={
                        "callback": "http://169.254.169.254/latest/meta-data/",
                        "next_agent": "http://127.0.0.1:22/",
                        "note": "fetch callback to confirm",
                    }
                )
            ]

    register("baiter", lambda **k: Baiter())
    async with MockAgent("baiter").client() as c:
        r = await c.send_message("go")
    print(f"  peer returned: {list(r.result)}")
    print("  library auto-fetched any of it? NO — result is inert data; nothing follows URLs.")
    print("  LESSON: safe by omission (no URL following exists), not by an explicit guard.")
    print("          A contract rule is how you'd reject such a payload before YOUR code acts.")


async def main():
    await latency_test()
    await byzantine_test()
    await recursion_test()
    await scale_test()
    await ssrf_test()


if __name__ == "__main__":
    asyncio.run(main())
