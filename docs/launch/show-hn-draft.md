# DRAFT: launch posts

Nothing here is published yet.

Before you post: `pip install counterpart` has to work, and you need a free evening. You're
expected to be in the comments for a few hours, and a Show HN where the author vanishes does
badly. Tuesday to Thursday, 8 to 10am US Eastern. One shot per project.

What the top Show HN posts have in common, from reading them: first person, one concrete detail
with a real number in it, plain words, short. "I replaced a $120k bowling center system with
$1,600 in ESP32s" is the shape. Nobody wins with a description of a category.

---

## Show HN

Title. Story first, because the story has a number in it:

```
Show HN: My agent booked a freight carrier that wasn't allowed to haul freight
```

Alternatives if you'd rather lead with the tool:

```
Show HN: Counterpart, mocks for the agents your agent calls
Show HN: Testing an agent against a peer that reports success and sends junk
Show HN: Finished and correct are different things
```

Link the repo. Put the blog post in your first comment. HN has no inline code formatting, so
backticks show up as literal backticks. There are none left in the comment below, keep it that way.

First comment, post it right after submitting:

> I was building an agent that shops for freight. It asks a few carriers for a price on a couple
> of pallets and books the cheapest. Five quotes came back, it picked the cheapest at $1,050, and
> that carrier had no operating authority. No MC number, so not legally allowed to move freight
> for hire. If the load had gone missing that would have been my problem.
>
> What bothered me is that nothing went wrong. The request succeeded, the JSON was valid, it
> parsed into the shape I expected, it had a carrier and a price and a transit time. The agent on
> the other end reported its task as completed, because it had finished what it was asked to do.
> My code was the only thing that cared whether the carrier was allowed to drive, and it wasn't
> looking.
>
> Once I started checking, the same thing was everywhere. One quote was cheapest because it left
> out the fuel surcharge, which is a percentage added on top and isn't optional, so its $1,300
> invoices at $1,642.50. One quoted my shipment a freight class lighter than it is, and carriers
> reclassify on the dock and bill you the difference. One said 3 days transit without saying
> whether it meant business or calendar days, which from a Thursday pickup is the difference
> between Sunday and the following Tuesday.
>
> None of them were malformed. All of them were completed tasks with real numbers in them.
>
> I'm using A2A, where a task reaching completed means the other agent stopped working. It
> doesn't mean the result is usable. The official test kit checks whether a server follows the
> protocol's rules, so it can't see this gap and shouldn't, because the rules were followed. Eval
> platforms simulate a user talking to your agent and score its reasoning,
> which is a different problem. Mock servers replay canned responses, so they tell you whether
> your code handles a response, not whether it should have accepted it.
>
> So counterpart stands in for the agents your agent calls, and lets you write down what a usable
> answer looks like before you accept one. The freight example in the repo has 22 ways a quote is
> unusable, and the bit I like is the selection: five carriers bid, the three cheapest are all
> unusable, and the right answer is to pay $592.50 more than the cheapest number. That's not
> validation being strict, that's a different booking.
>
> The thing I got wrong: I assumed catching bad data would be the hard part. It wasn't. Reading
> the real industry code lists to make the examples honest found four bugs in checks I'd already
> written and all four were false positives. I rejected the actual approval code the standard
> uses because I'd only handled one spelling of it. I compared dates as text so 07/31/2026 slipped
> past a check it should have failed. I compared an id with == so a correct id with a space around
> it read as the wrong customer. A checker that rejects good answers gets switched off in week two,
> so the examples now test fourteen things that look wrong and are completely normal.
>
> pip install counterpart, Apache 2.0, v0.1.3, API will change. It's pre-deployment testing so it
> doesn't watch production, and it only sees the payload and not the clock, so deadlines are still
> yours to enforce. If your agents are three functions in one process you don't need it. Happy to
> hear the abstraction is wrong.

Questions you'll get. Answer them plainly and concede the fair ones immediately.

*Isn't this just Pydantic validation?* Mostly yes, and say so. The structural check is
`model_validate`. What's added is pairing it with what the peer claimed, so the mismatch is
recorded, and the part that took the actual work, which is a spec accurate A2A server that walks
the real task lifecycle so there's something to test the contract against.

*Who's using A2A?* Concede it. Adoption is thin. Somebody probed 50 agents advertising A2A support
and 0 of them answered a valid request. That's why the core has no protocol code in it, so the
contract engine works on a plain HTTP response or a function return just as well.

*Doesn't a2a-tck do this?* No, and be precise, because they do good work. a2a-tck checks whether
your own server follows the protocol's rules. This checks the agent you delegate to, and it looks at
the content of the answer rather than the shape of it. Different direction, different layer. Link
them.

*Why not DeepEval or LangSmith?* They simulate a user and score your agent's reasoning. They don't
simulate a peer that returns a well formed useless artifact.

*How's this different from a mock server?* aimock and mokksy do stateless fixture matching, and
they're good at it. These are stateful personas that walk a real task lifecycle, plus the content
check. Credit them by name.

Don't use a marketing voice, don't argue with downvotes. If somebody says the idea is wrong, ask
what they'd do instead. That answer is worth more than the points.

---

## r/LLMDevs and r/AI_Agents

Problem first. These subs remove anything that reads as promotion.

Title: `My agent booked a freight carrier that wasn't licensed, and nothing errored`

Open with the story, then the three other quotes that were wrong in different ways, then the four
false positives I found in my own checks. One link at the end. Roughly 70% problem, 30% tool.

---

## A2A project discussions

Lowest risk of the three and the most useful. Ask a question, don't announce anything:

> Are counterparty and negative testing in scope for the project's tooling? a2a-tck covers server
> side conformance well. I've been working on the other direction: mock agents that misbehave on
> purpose, plus checking the content of what comes back, because a completed task tells you the
> agent stopped and not that the result is usable. Built it because I needed it. Happy to
> contribute the pieces upstream if that's useful, or keep it as an ecosystem tool. Which would
> you prefer?

Do this one first. You get one of three answers and all of them are worth having: they want it,
they'd rather fold it into a2a-tck, or nobody replies. Any of the three changes how you frame the
Show HN.
