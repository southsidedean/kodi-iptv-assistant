# The chat page

kia has a chat box on the Dashboard, under **Ask kia**. You type a question about your own TV
setup in plain words, and kia answers by looking things up with its own tools:

- "What football is on tonight?"
- "Why does channel 400 have no guide?"
- "Which of my playlists has the most channels?"
- "Is anything wrong with my setup right now?"

On its own the chat only reads and explains; it changes nothing. When something needs fixing, it
tells you what to do and points to the right page or doc. If you want, you can let it ask to do a
few of those things for you, each waiting for your approval. See [letting the chat
act](#letting-the-chat-act) below.

The chat talks to an AI provider, which you pay for. It is off until you turn it on, and it is
the one part of kia that costs money to use, so kia caps what the household can spend.

## What you need

- The **gateway** part, which holds the provider's key and keeps the running total of spend. The
  chat will not work without it.
- An account with an AI provider, and a key from them. kia uses Claude (Anthropic) by default.
- For more than one person, the **front door**, so everyone signs in once and the chat knows who
  is asking. On a single-admin setup you can use it with kia's own password instead. See
  [one login for the stack](front-door.md).

## Turning it on

Add these to your `.env` (see [all the settings](configuration.md)):

```sh
KIA_CHAT=true
KIA_CHAT_PROVIDER_KEY=your-provider-key
KIA_CHAT_BUDGET=5
```

Then apply the stack:

```sh
scripts/stack.sh apply
```

That writes the gateway's AI route and makes a key for the household, and the **Ask kia** page
starts answering. The provider's key stays in the gateway; it never reaches kia or the gateway's
settings file.

## Letting the chat act

By default the chat only reads. You can let it reach four of kia's tools that do something:
refresh the guide, test a channel, add a channel to Plex, and check a change to your stack. Turn
this on with one more line in your `.env`:

```sh
KIA_CHAT_ACTIONS=true
```

Recreate kia so it picks up the setting (`docker compose up -d kia`).

The chat never makes a change on its own. When you ask it to do one of these, kia leaves a request
for you to approve on the Dashboard, and the chat shows a note under its reply with a link straight
to it. Nothing happens until you say yes. This is the same path, and the same approvals, that kia
offers AI assistants over MCP, so [asking kia to act](mcp.md#asking-kia-to-act) has the details of
what each tool does and how a request behaves.

If you would rather let some of these run without asking you each time, the household's `[asks]`
`auto_approve` policy in `kia.toml` decides, exactly as it does for MCP. A kind listed there runs
on its own and still shows on the Dashboard, marked approved automatically. [Letting requests run
by themselves](mcp.md#letting-requests-run-by-themselves) covers the policy and its hourly limit,
and the same safety thinking applies here: the kinds that can run alone only test, refresh, or
check, so the worst case is extra work for kia and the streams' servers.

Leaving `KIA_CHAT_ACTIONS` out keeps the chat read-only.

## What it costs

`KIA_CHAT_BUDGET` is the most the household may spend a month, in US dollars (5 by default). The
gateway adds up what each question costs and stops the chat once the total reaches the cap; the
page says so, and it starts again when the month turns. Set the figure where a surprise would not
hurt, and raise it if you find the chat worth more.

Two things keep everyday questions cheap. kia uses a smaller, cheaper model by default, and turns
on a stronger one only for a question that asks for it (the **Use the stronger model** box under
the chat). And it caches the parts of each request that do not change, which the provider bills at
a lower rate. You can change the two models with `KIA_CHAT_MODEL` and `KIA_CHAT_MODEL_HEAVY`.

## What the provider sees

Your question goes to the provider, along with whatever kia's tools return for it: channel names,
guide listings, how your last refresh went, and the like. It does not send your playlists' private
addresses or any password or token; kia hides logins in every address first, as it does for its
other AI help ([asking kia for help](ask-kia.md)).

The chat keeps its history in your browser, not on kia, and sends only the recent turns with each
question. **Clear chat** on the page forgets it.

## Under the hood

kia runs the chat itself: it takes your question, calls its own read-only tools to gather the
facts, and asks the model to put the answer in plain words, looping until the answer is ready. With
chat actions on, the four acting tools join the read-only ones, and anything the model asks to do
becomes a Dashboard request rather than running from the chat. The model is reached through the
gateway, which holds the provider's key, meters the spend against the budget, and keeps a log of
each call. kia only ever sends the gateway the household's own key, so the provider's key stays in
one place.

This is the same set of tools kia offers AI assistants over [MCP](mcp.md). The difference is that
the chat page is kia's own, needs no setup on your devices, and works from any browser that can
reach the Dashboard.
