# DRAFT: launch blog post

Not published yet. Re-check every factual claim on the day you post, because the FHIR
implementation guide is a moving target and being exactly right is the whole point of this
piece.

---

## "Complete" doesn't mean approved

I've been building a testing library for agents that talk to each other, and I went looking for
a realistic example of the failure I care about most: an agent that reports success while
handing back something you can't use.

I found the cleanest example I've seen sitting inside an official healthcare standard.

### The setup

HL7's Da Vinci Prior Authorization Support guide is the FHIR standard for a provider asking an
insurer to authorize a procedure. The insurer answers with a `ClaimResponse`, which has a top
level field called `outcome`.

That field is bound to a short list of values: `queued`, `complete`, `error`, `partial`. It's
required, cardinality 1..1. If you're writing a client, it looks exactly like the field you're
supposed to branch on.

### The problem

Here is the guide's own published example of a *pended* response. Pended means the request
hasn't been decided yet. It's sitting in clinical review.

```json
{
  "resourceType": "ClaimResponse",
  "outcome": "complete",
  "item": [{
    "adjudication": [{
      "extension": [{
        "url": ".../extension-reviewAction",
        "extension": [{
          "url": "number",
          "valueCodeableConcept": {
            "coding": [{
              "system": "https://codesystem.x12.org/005010/306",
              "code": "A4",
              "display": "Pending"
            }]
          }
        }]
      }]
    }]
  }]
}
```

`outcome` says `complete`. There's no `preAuthRef`, so no authorization number. The only thing
in the whole payload telling you this wasn't approved is `reviewActionCode: "A4"`, four levels
deep inside an extension.

Now look at the guide's *approved* example. It's also `outcome: "complete"`. It also has no
`preAuthRef`. The two responses, approved and still pending, differ only in that review action
code.

So the field that tells you the truth is optional, and the field that misleads you is
mandatory. A client reading the obvious top level status can't tell an approval from a pend. It
will conclude the authorization came through, and the procedure gets scheduled against an
authorization that doesn't exist.

I checked whether this was an old mistake since fixed. It's in STU 2.1 and it's still in the
current build.

### It isn't hypothetical

X12 has the same trap from another direction. A first `278` response is often an interim
acknowledgement rather than a decision. Blue Cross NC returns `HCR01=A4`, pended, within 24
hours, and sends the real determination later in a separate unsolicited transaction. Texas
Medicaid goes further and returns `A4` for all approved transactions, so there `A4` just means
your request arrived.

Same code. Opposite meanings. Depends on the payer.

The money is real too. Premier reports that 10.4 percent of denied claims had been pre-approved
through prior authorization, up from 3.2 percent the year before, at $57.23 per claim just to
rework. Getting an authorization is not the same as getting paid.

### Why I care beyond healthcare

I work on agent-to-agent systems, where this shape turns up everywhere. In the A2A protocol, a
task reaching state `completed` means the agent finished its work. It doesn't mean the work is
correct, or complete, or usable. Those are different claims and only one of them is on the wire.

This is worse than a crash. A crash gives you a stack trace and somewhere to look. This gives
you nothing. Every agent in the chain reports success, no error is logged anywhere, and you find
out weeks later from a denied claim or a customer billed the wrong amount. An academic study of
multi-agent failures puts false success at 45 to 79 percent of them.

Conformance testing can't see it, because conformance asks whether the protocol behaved, and it
did. Evaluation platforms can't see it either, because they simulate a user talking to your
agent and score its reasoning. Neither one asks the question that actually matters: the agent I
delegated to says it's done, can I use what it gave me?

### What I built

[counterpart](https://github.com/edrisibra/counterpart) does two things.

It gives you counterparties that misbehave on purpose, so you can test against a peer that lies
about finishing:

```python
peer = mock_agent(persona="false_success")   # reports completed, returns garbage
```

And it lets you say what a usable answer looks like, then check the answer you got:

```python
contract = (Contract("prior authorization")
    .returns(AuthDetermination)
    .require("certified", is_approval)          # reads the review action code, not `outcome`
    .require("has_auth_number", lambda a: bool(a.authorization_number))
    .expect_status("completed"))

report = contract.verify(result=payload, reported_status=task.status)
assert report.contract_violated    # the peer claimed success, the work doesn't hold up
```

The report records what the peer claimed next to whether it held up, which is what makes this
kind of failure visible instead of invisible.

The repo has the scenario above as something you can run: a clinic's agent clearing a procedure
with an insurer's eligibility and utilization management agents, against 25 modelled payer
responses that all report `completed`. A naive agent schedules on 24 of them.

### The part that surprised me

Writing the checks was easy. Writing checks that don't cry wolf was the hard part.

Reading the actual X12 value sets turned up three bugs in the contracts I'd written, and every
one of them was a false positive. My checks were rejecting valid answers.

I only accepted the literal string `"APPROVED"`, so I rejected `A1` and `"Certified in total"`,
which are the real X12 certification values. I compared dates as strings, so a payer sending
`07/31/2026` quietly passed a window check it should have failed, which is the dangerous
direction to get wrong. And I compared member ids with `==`, so a payer echoing back
` w123456789 `, the correct patient with different padding, got rejected as the wrong person.

That's the real lesson and it generalizes well past healthcare. A checker that flags legitimate
variation gets switched off in week two, and then you have no checker at all. Both scenarios in
the repo now test for false positives explicitly, not just for catches.

### Honestly

This is version 0.1.0 and the API will change. It's testing you run before you deploy, so it
doesn't watch production and it doesn't solve agent identity or trust. If your agents are three
functions in the same process, you don't need it.

But if you have an agent depending on an agent somebody else operates, it's worth asking what
your code does when that agent says `completed` and means something else.

---

## Notes to self, delete before publishing

- Re-verify the PAS examples the morning you publish and link the raw JSON directly.
- The three false positives section is the most credible thing in here, because it's a real
  finding against my own code. Don't cut it for length.
- Don't claim any adoption. There isn't any yet.
- Title options: "Complete doesn't mean approved" / "The field that says success is required.
  The field that says the truth is optional." / "Your agent said it's done. Prove it."
