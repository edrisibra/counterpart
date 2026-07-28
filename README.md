# counterpart

[![CI](https://github.com/edrisibra/counterpart/actions/workflows/ci.yml/badge.svg)](https://github.com/edrisibra/counterpart/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/counterpart.svg)](https://pypi.org/project/counterpart/)
[![Python](https://img.shields.io/pypi/pyversions/counterpart.svg)](https://pypi.org/project/counterpart/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

Mock [A2A](https://a2a-protocol.org/) counterparty agents, and verify what they send back.

If your agent delegates work to somebody else's agent, you cannot test it without them.
counterpart gives you stand-in counterparties to point it at instead: ones that work, ones that
stall, ones that come back with junk. It also checks the replies, so a peer that reports success
while returning nothing usable fails your test rather than passing it.

```bash
pip install counterpart
```

```python
async def test_peer_lies_about_finishing(mock_agent):
    peer = mock_agent("false_success")     # reports done, returns garbage

    task = await peer.ask("Quote 2 pallets LA to Dallas", contract={"price": float})

    assert task.status == "completed"   # the peer said it finished
    assert task.contract_violated      # what it sent back was unusable
```

Installing the package is the whole setup. The `mock_agent` fixture and the async
configuration come with it, so there is no `conftest.py` to write.

`{"price": float}` is shorthand for a contract that requires a `price` field which is a
number. When you want more, build one properly. Pass a pydantic model, add named checks, and
use `.client()` when a test needs several turns on one connection:

```python
contract = (
    Contract("freight quote")
    .returns(Quote, strict=True)                       # a real model, no coercion
    .require(price_positive=lambda q: q.price > 0)
)

peer = mock_agent("clarifier", question="Deliver by when?")
async with peer.client() as client:                    # one connection, several turns
    task = await client.send_message("Quote 2 pallets", contract=contract)
    task = await client.reply(task, "Friday")
```

## Why check the reply as well

Mocking the transport is the easy half. The reason counterpart also looks at content is that
`completed` only tells you the other agent stopped working. Every one of these arrives as a
completed task, and the protocol is satisfied by all of them:

```python
{"message": "Your quote is ready!"}          # reads fine, contains no price
{"price": "call for rate"}                   # a sentence where you needed a number
{"price": 1420.0, "account": "AC-99812"}     # right shape, somebody else's account
{}                                           # nothing at all
```

Your code then carries on with whatever it got. Nothing raised, nothing was logged, and the bad
value is now three functions downstream or in an invoice. That is what makes these expensive to
find: you are debugging backwards from a wrong number, days later, with no stack trace to start
from. One study of multi-agent failures attributes 45 to 79 percent of them to this rather than
to anything crashing.

A contract is just you writing down what you were expecting, so the test fails at the point the
peer lets you down instead of somewhere later.

## Personas

Every persona is deterministic, needs no LLM, and drives a real A2A task lifecycle.

| Persona | What it does |
| --- | --- |
| `cooperative` | Works and completes with a well formed result |
| `clarifier` | Asks one question, waits for your answer, then completes |
| `false_success` | Reports `completed` and returns garbage |
| `resource_abuse` | Stalls or streams forever without finishing |
| `flaky` | Drops the connection, then recovers on retry |
| `over_sharing` | Asks for more context than the task needs |

Writing your own takes one class and one call. There is no DSL.

```python
from counterpart.core import Complete, Progress
from counterpart.personas import register

class HalfAnswer:
    def respond(self, turn, ctx):
        return [Progress("working"), Complete(result={"partial": True})]

register("half_answer", HalfAnswer)
```

## One thing to know about contracts

By default `returns()` uses pydantic's normal mode, so a peer sending `{"price": "1420.00"}`
gets coerced to a float and passes. Your predicates then see a real number, so an `isinstance`
check will not save you. That is ordinary pydantic behaviour, but it surprises people here,
because you might reasonably think you asked for type validation.

If you want the stricter reading, ask for it:

```python
Contract("fare").returns(Fare, strict=True)
```

Lax is the default on purpose. Plenty of real services send numbers as strings, and a contract
that rejects valid traffic gets switched off, which leaves you with nothing. Use `strict=True`
when you control both ends or the format is pinned.

## Exposing your own agent

`wrap()` turns any callable, sync or async, into an A2A server you can point tests at.

```python
from counterpart import wrap

app = wrap(my_agent, name="quoting-agent", skills=["freight-quote"])
```

Run it with uvicorn, or skip the socket entirely with `A2AClient(app=app)`.

## Checking a live agent

```bash
counterpart check https://my-agent.example.com
counterpart attack https://my-agent.example.com --json
```

`check` gives you a scored report where every row cites the spec section it verifies. It is a
quick smoke test for the development loop, not a certification suite. For the full conformance
matrix use the official [a2a-tck](https://github.com/a2aproject/a2a-tck). `attack` sends
adversarial probes and reports what came back.

## Examples

There are four runnable scenarios in [examples](examples), each in a domain where the
counterparty genuinely belongs to someone else, and each built around a different kind of
failure.

[Freight procurement](examples/freight_procurement.py) collects quotes from competing carriers
and picks the cheapest usable one. The naive version books the carrier whose completed quote
lists `price` as `"call for rate"`, and sends that string to invoicing.

[Prior authorization](examples/prior_authorization.py) is a clinic getting a procedure approved
by a health insurer before performing it, which is a real cross-company agent problem with money
and patient safety attached. It models 25 insurer responses that all report `completed`, taken
from the healthcare messaging standards and from billing forums where people describe what
actually goes wrong. Each is labelled by how well attested it is, and two are marked unattested
because I could not find a real case for them. One of them is worth the click: the standard's own
published example of a *pending* decision reports success, with the only honest signal buried in
an optional nested field.

[Satellite downlink](examples/satellite_downlink.py) schedules a pass with a ground station
network. Twelve plans that all validate and all describe a different physical reality: a window
handed over in GPS time, elevation in radians where the field says degrees, an inertial frame
where you need an earth fixed one. This is the failure that cost NASA the Mars Climate Orbiter.

[Chaos multihop](examples/chaos_multihop.py) is deliberately excessive. Four hops, each a
separate HTTP server, auth passed along the chain, twenty concurrent users, and corruption
injected at the deepest hop. Corruption three hops away still gets caught at the top.

Each one checks two things, and the second matters more. Every unusable answer has to be caught,
and every legitimate answer has to be left alone.

That second half is where the real difficulty turned out to be. Going through the actual industry
value sets found four bugs in the contracts I had written for these examples, and every single one
was a false positive. My checks were rejecting good answers: an approval code I had not thought to
accept, an id that came back correct but padded with spaces, and a date compared as text so that
`07/31/2026` slipped past a check it should have failed. None of the four was a missed catch.

That is the trap with this kind of testing. Catching bad data is easy. A checker that also flags
good data gets switched off, and then you are back to having none, so both examples test for false
positives explicitly.

## What it does not do

This is testing you run before you deploy. It does not watch production, it does not solve
agent identity or trust, and it will not bound how deep a peer delegates. A contract only sees
the payload, not the clock, so if a correct answer arriving 400ms late is useless to you, that
timeout is yours to enforce. [limits_probe.py](examples/limits_probe.py) shows exactly where
the library goes quiet.

If your agents are three functions in the same process, you do not need any of this.

## Design

The core has no protocol code in it. A2A is one adapter, and a test enforces the boundary, so
the contract engine also works on a plain HTTP response, an MCP style tool result, or a
function return.

- [docs/spec-notes.md](docs/spec-notes.md) maps A2A v1.0 to the code, citing the spec
  throughout. Wire types are checked against the vendored normative proto.
- [docs/prior-art.md](docs/prior-art.md) covers what already exists and where this fits.
- [docs/roadmap.md](docs/roadmap.md) lists what is out of scope and what is known to be missing.

## Status

Version 0.1.2, built against A2A spec v1.0. The API will change. Apache-2.0.

If you have hit this failure in something you built, I would like to hear about it. Open an
issue with the shape of the response that fooled you.
