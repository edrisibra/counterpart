# DRAFT: launch posts

Nothing here is published. Before any of it goes out:

1. `pip install counterpart` works and the repo is public. Both are true now.
2. You have a free evening. You're expected to answer comments for a few hours, and a Show HN
   where the author disappears does badly.

Timing: Tuesday to Thursday, roughly 8 to 10am US Eastern. You get one shot per project, so
don't burn it on a Friday.

---

## Show HN

Title, under 80 characters, leading with the finding rather than the tool:

```
Show HN: HL7's prior-auth standard says "complete" when a request is only pended
```

Alternatives, roughly in order of preference:

```
Show HN: counterpart, test your agent against a peer that lies about finishing
Show HN: The field that says success is required. The field with the truth is optional.
Show HN: Your agent said it's done. Prove it.
```

Link the GitHub repo, not the blog post. HN prefers the artifact. Put the blog link in your
first comment.

First comment, posted immediately after submitting:

> I've been building a testing library for agents that talk to each other over A2A, and went
> looking for a realistic example of the failure I care about: an agent reporting success while
> returning work you can't use. The cleanest example I found is in HL7's own prior
> authorization standard.
>
> Their published pended example returns `outcome: "complete"` with no authorization number.
> Their approved example is also `outcome: "complete"` with no authorization number. The two
> differ only in an optional, deeply nested review action code (A1 certified vs A4 pended),
> while `outcome`, the field that misleads you, is required. A client branching on the obvious
> top level status can't tell an approval from a pend. It's still in the current build, not an
> old mistake since fixed.
>
> X12 has the same trap. A first 278 response is often an interim ack rather than a decision.
> Blue Cross NC returns A4 (pended) within 24 hours and sends the real determination later as a
> separate unsolicited transaction. Texas Medicaid returns A4 for all approved transactions.
> Same code, opposite meaning, depending on the payer.
>
> The general shape is what interests me. In A2A a task reaching `completed` means the agent
> finished, not that the work is right. Conformance testing can't see the difference because
> the protocol behaved fine, and eval platforms can't either because they simulate a user and
> score reasoning. Nobody checks whether the artifact the peer handed back is usable.
>
> So the library does two things: counterparties that misbehave on purpose, so you can test
> against a peer that lies, and contracts that verify the returned content against what you
> asked for. The repo has a runnable scenario with 25 modelled payer failures, all reporting
> `completed`, where a naive agent schedules on 24 of them.
>
> The part that surprised me: writing checks was easy, writing checks that don't cry wolf was
> not. Reading the real X12 value sets found three bugs in my own contracts and all three were
> false positives. I rejected `A1` and "Certified in total", which are real certification
> values. I string-compared dates so `07/31/2026` quietly passed a check it should have failed.
> I `==`-compared member ids so a correctly echoed ` w123456789 ` was rejected as the wrong
> patient. A checker that flags valid answers gets switched off in week two, which is worse
> than having none.
>
> v0.1.0, Apache-2.0, API will change. It's pre-deployment testing, so it doesn't monitor
> production and doesn't solve agent identity. If your agents are three functions in one
> process you don't need it. Happy to be told the abstraction is wrong.

Questions you'll get, with honest answers ready.

*Isn't this just Pydantic validation?* Largely yes, and say so. The structural check is
`model_validate`. What's added is pairing it with the peer's reported status so the discrepancy
is recorded, a typed failure category, and the actual work, which is a spec-verified A2A server
that walks the real task lifecycle so you have something to test the contract against.

*Who even uses A2A?* Concede it. Adoption is thin. A probe found 0 of 50 agents advertising A2A
actually answered a valid request. That's why the core is protocol-agnostic: the contract engine
works on any delegated result, including plain HTTP, an MCP tool call, or a function return.

*Doesn't a2a-tck already do this?* No, and be precise. a2a-tck tests your server for protocol
conformance. This tests the counterparty you delegate to, at the content layer. Different
direction, different layer. Link them, don't disparage them.

*Why not DeepEval or LangSmith?* They simulate a user and score your agent's reasoning. They
don't simulate a protocol-speaking peer returning a malformed artifact.

*How is this different from a mock server?* aimock and mokksy do stateless fixture matching.
These are stateful personas walking a real task lifecycle, paired with content verification.
Credit them by name.

Rules of engagement: no marketing voice, concede fair criticism immediately, never argue with a
downvote. If someone says the idea is wrong, ask what they'd do instead. That answer is worth
more than the upvotes.

---

## r/LLMDevs and r/AI_Agents

Problem first. These subs remove posts that read as "check out my project".

Title: `The worst multi-agent bug isn't a crash, it's an agent reporting "completed" with garbage`

Open with the HL7 finding, then the general shape, then the three false positives you found in
your own checks, then one link at the end. Roughly 70 percent problem, 30 percent tool.

---

## A2A project discussions

The most useful of these posts and the lowest risk. Frame it as a question rather than an
announcement:

> Is counterparty and negative testing in scope for the project's tooling? a2a-tck covers
> server side conformance well. I've been working on the other direction, simulated
> counterparty agents that misbehave (pended-as-approved, silent code downgrade, never decides)
> plus content verification of the returned artifact. Built it out of need. Happy to contribute
> the pieces upstream if that's useful, or keep it as an ecosystem tool. Which would you prefer?

Do this one before the Show HN if you can. The answer changes how you frame the launch, and you
get one of three useful replies: they want it, they'd fold it into a2a-tck, or nobody answers.
