# DRAFT: launch blog post

Not published. Check the numbers against the example before posting, and run
`uv run python examples/freight_edge_cases.py` so the output you quote is the output people get.

---

## My agent booked a carrier that wasn't allowed to haul freight

I was building an agent that shops for freight. It asks a handful of carriers what they'd charge
to move a couple of pallets, then books the cheapest one. Five quotes came back. It picked the
cheapest, at $1,050.

That carrier had no operating authority. No MC number, which is the registration a carrier needs
to legally move freight for hire in the US. If the load had gone missing, the loss would have
been mine, and my insurer would have had a reasonable opinion about why.

Here's the thing that bothered me. Nothing went wrong.

The request succeeded. The response was valid JSON. It parsed cleanly into the shape I expected.
It had a carrier name, a price, a transit time, an expiry date. The agent on the other end
reported its task as `completed`, because from its point of view it had finished the work it was
asked to do. Every layer I had was satisfied.

My code was the only thing that cared whether the carrier was allowed to drive, and my code
wasn't looking.

## The bug isn't in the protocol

I'm using [A2A](https://a2a-protocol.org/), the protocol for agents to hand work to each other.
When a task reaches `completed`, that means the other agent stopped working. It does not mean the
result is any good. Those are two different claims and only one of them is on the wire.

Once I started looking for this, it was everywhere in the quotes I was getting back:

One carrier came in cheapest because the quote excluded the fuel surcharge, which in freight is a
percentage added on top and is not optional. Its $1,300 would have invoiced at $1,642.50, about a
quarter more than the number I compared against.

Another quoted my shipment at freight class 50 when it's class 70. Class comes from density, and
a carrier that reclassifies your pallet on the dock bills you the difference. The cheap number
was cheap because it described a different, lighter shipment.

Another said three days transit and did not say which kind of days it meant. Transit is normally
quoted in business days, so from a Thursday pickup that is the following Tuesday. I had been
planning around Sunday. Nothing in the payload said which.

None of those were malformed. Every one of them was a completed task with a valid payload and a
real number in it. If you sort by price and book the winner, you lose money on all three, and you
find out weeks later from an invoice that doesn't match anything you agreed to.

## Why the tools I had didn't help

I checked whether I was missing something obvious.

There's an official test kit for A2A that checks whether a server follows the protocol's rules.
It didn't help, because the carrier's server followed them. That is the whole point of that kit and
it did its job. It will happily tell you a server is correct while the server hands you a quote
from a carrier that isn't allowed to drive.

Evaluation platforms didn't help either. They simulate a user talking to your agent and score how
well it reasons. Useful, different problem. Nobody was checking whether the thing the *other*
agent sent back was usable.

Mock servers got closest, and there are a couple of good ones. But they replay canned responses,
so they answer "does my code handle a response" and not "should my code have accepted this
response".

The question I actually had was: the agent I delegated to says it finished, can I use what it gave
me? Nothing I could find asked it.

## What I built

[counterpart](https://github.com/edrisibra/counterpart) does two things.

It stands in for the agents your agent calls, and it lets you say what a usable answer looks like
before you accept one.

```python
async def test_agent_rejects_a_bad_quote(mock_agent):
    peer = mock_agent("false_success")     # reports done, sends back junk

    task = await peer.ask("Quote 2 pallets LA to Dallas", contract={"price": float})

    assert task.status == "completed"   # it said it finished
    assert task.contract_violated      # and there was no price in it
```

For the real freight case the contract is longer, because the things that cost money are specific:

```python
contract = (
    Contract("LTL freight quote")
    .returns(Quote)
    .require(has_operating_authority=lambda q: bool(q.mc_number) and q.mc_number.isdigit())
    .require(insurance_valid=lambda q: date.fromisoformat(q.insurance_expires) >= today)
    .require(fuel_surcharge_accounted=lambda q: q.fuel_surcharge_usd is not None)
    .require(freight_class_matches=lambda q: q.quoted_freight_class == load.freight_class)
    .require(transit_basis_stated=lambda q: q.transit_day_basis in {"business", "calendar"})
)
```

That's not clever code. It's the list of things I now know to check, written down where a test can
run it, instead of living in my head and being forgotten at 6pm.

The repo has this as a runnable example with 22 ways a quote can be unusable. The result I like
best is the selection one, because it isn't about validation at all:

```
  $1,050.00  Gray Route          no operating authority
  $1,180.00  Dockline            excludes the fuel surcharge
  $1,240.00  Sunbelt LTL         wrong freight class, rebilled on the dock
  $1,642.50  Ridgeline Freight   usable
  $2,180.00  Meridian Carriers   usable

  without checks: books Gray Route at $1,050.00
  with checks:    books Ridgeline Freight at $1,642.50
```

The correct answer is to pay $592.50 more. That's the part I'd want someone to take away. This isn't
a linter that makes your tests stricter, it's the difference between two bookings.

## The part I got wrong

I expected the hard bit to be catching bad data. It wasn't. It was not crying wolf.

I went and read the actual industry code lists to make the examples realistic, and doing that
found four bugs in the checks I had already written. Every single one was a false positive. My
checks were rejecting good answers.

I only accepted one spelling of an approval code, so I rejected the real code that the standard
actually uses. I compared dates as text, so `07/31/2026` slipped past a check it should have
failed, which is the dangerous direction to be wrong in. I compared an id with `==`, so a
correct id that came back with a space around it was treated as the wrong customer. And I rejected
`currency: "usd"` because I'd only thought of `"USD"`.

Not one of the four was a missed catch. All four were the checker being wrong about good input.

That matters more than it sounds. A checker that flags valid traffic gets switched off in week
two, and then you have nothing. So both examples in the repo now test the opposite direction
explicitly: fourteen things that look wrong and are completely normal, like an all in rate with no
breakdown, or a billed weight higher than the scale weight because the pallet is bulky, and all
fourteen have to pass.

## What it doesn't do

It's testing you run before you ship. It doesn't watch production, so if you want to know what
your agents are doing right now you want tracing, not this.

It also only sees the payload, not the clock. A correct answer that arrives 400ms too late is
still useless to you, and the contract can't tell. That timeout is yours to enforce.

And if your agents are three functions in the same process, you don't need any of this. Call the
functions.

## If you've hit this

I'd like to know what the payload looked like. The failure modes in the examples are only as good
as the real cases behind them, and I'd rather model things that actually happened to somebody
than things I imagined. There's an issue tracker.

`pip install counterpart`, Apache 2.0, version 0.1.3, the API will change.

---

## Notes to self, delete before posting

- Run the freight example and paste its real output. Don't retype the numbers.
- The four false positives section is the most credible thing here because it's an admission
  against interest. Keep it even if the post runs long.
- Don't claim any users. There aren't any.
- Title options, all plain: "My agent booked a carrier that wasn't allowed to haul freight" /
  "The cheapest quote came from an unlicensed carrier" / "Finished and correct are different
  things".
