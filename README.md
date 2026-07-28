# counterpart

[![CI](https://github.com/edrisibra/counterpart/actions/workflows/ci.yml/badge.svg)](https://github.com/edrisibra/counterpart/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/counterpart.svg)](https://pypi.org/project/counterpart/)
[![Python](https://img.shields.io/pypi/pyversions/counterpart.svg)](https://pypi.org/project/counterpart/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](https://github.com/edrisibra/counterpart/blob/main/LICENSE)

---

[A2A](https://a2a-protocol.org/) lets one AI agent hand work to another. counterpart plays the
agent on the other side, from helpful to hostile, and catches the replies your code should never
accept:

```python
async def test_carrier_reports_success_with_no_price(mock_agent):
    peer = mock_agent("false_success")     # reports done, returns garbage

    task = await peer.ask("Quote 2 pallets LA to Dallas", contract={"price": float})

    assert task.status == "completed"   # the peer said it finished
    assert task.contract_violated      # what it sent back was unusable
```

## Why counterpart

The agents your agent depends on belong to other people. You cannot spin up a carrier's agent
or an insurer's agent to test against, so counterpart stands in for them. And it reads what they
send back, because `completed` only means the other agent stopped working. Every one of these
arrives as a completed task with the protocol fully satisfied:

```python
{"message": "Your quote is ready!"}          # reads fine, contains no price
{"price": "call for rate"}                   # a sentence where you needed a number
{"price": 1420.0, "account": "AC-99812"}     # right shape, somebody else's account
{}                                           # nothing at all
```

A contract is you writing down what you were expecting, so the test fails at the point the peer
lets you down instead of three functions later or in an invoice. `{"price": float}` is the
shorthand form. For real checks, pass a pydantic model and add named predicates, with
`strict=True` when you do not want `"1420.00"` silently coerced to a number:

```python
contract = (
    Contract("freight quote")
    .returns(Quote, strict=True)
    .require(price_positive=lambda q: q.price > 0)
)
```

The mock runs inside your test process. No socket, no port, nothing on the network. `mock_agent`
is the pytest fixture; `MockAgent` is the same object without pytest, and its `.serve()` binds a
real address when you need to point another process at it.

It also works in the other direction, with the mock as the caller and your agent answering.
`wrap()` turns any function into an A2A server, and the same contract now judges your answers:

```python
from counterpart import MockAgent, serve_asgi, wrap

with serve_asgi(wrap(my_agent, name="quoting-agent")) as url:
    peer = MockAgent("cooperative")
    task = await peer.send_task(url, "Quote 2 pallets", contract={"price": float})

assert not task.contract_violated   # your own answer held up
```

If your agents are three functions in the same process, you do not need any of this.

## Personas

Six ship with the package. All deterministic, none needing an LLM.

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

## Installation

```bash
pip install counterpart
```

Installing the package is the whole setup. The `mock_agent` fixture and the async configuration
ship with it, so there is no `conftest.py` to write. Python 3.11+.

There is also a CLI for poking a live agent: `counterpart check <url>` scores it against the
spec, section by section, and `counterpart attack <url>` sends deliberately malformed requests
and reports what came back. For the full rulebook use the official
[a2a-tck](https://github.com/a2aproject/a2a-tck).

## Examples

Five runnable scenarios in [examples](https://github.com/edrisibra/counterpart/tree/main/examples), each in a domain where the counterparty
genuinely belongs to somebody else:

- [Freight procurement](https://github.com/edrisibra/counterpart/blob/main/examples/freight_procurement.py): pick the cheapest usable quote, not the
  cheapest string that parses.
- [Freight edge cases](https://github.com/edrisibra/counterpart/blob/main/examples/freight_edge_cases.py): 22 ways a quote is unusable, 14 that look
  wrong and are fine. Checking them changes which carrier gets booked, at $592.50 more.
- [Prior authorization](https://github.com/edrisibra/counterpart/blob/main/examples/prior_authorization.py): 25 insurer replies that all report
  success, including the standard's own example of a pending decision that does.
- [Satellite downlink](https://github.com/edrisibra/counterpart/blob/main/examples/satellite_downlink.py): twelve plans that all validate and each
  describes a different physical reality. Radians where the field says degrees.
- [Chaos multihop](https://github.com/edrisibra/counterpart/blob/main/examples/chaos_multihop.py): four hops, real sockets, twenty concurrent users,
  corruption injected at the bottom and caught at the top.

Every example tests both halves: each unusable answer is caught, and each legitimate answer
is left alone. The second half is harder. All four bugs found in these contracts were false
positives, checks rejecting good answers, and a checker that flags good data gets switched off.

## Documentation

- [docs/spec-notes.md](https://github.com/edrisibra/counterpart/blob/main/docs/spec-notes.md) maps A2A v1.0 to the code, citing the spec throughout.
- [docs/prior-art.md](https://github.com/edrisibra/counterpart/blob/main/docs/prior-art.md) covers what already exists and where this fits.
- [docs/roadmap.md](https://github.com/edrisibra/counterpart/blob/main/docs/roadmap.md) lists what is out of scope, and
  [limits_probe.py](https://github.com/edrisibra/counterpart/blob/main/examples/limits_probe.py) demonstrates it: no production monitoring, no
  agent identity, and a contract sees the payload, not the clock.

## Contributing

Version 0.1.13, Apache-2.0, the API will change. If a peer's reply has fooled something you
built, open an [issue](https://github.com/edrisibra/counterpart/issues) with the shape of the
payload. That is the most useful contribution this project can get.
