# Privacy

When you ask Claude a question in Steward, Claude sees the figures it needs: balances, income, spending totals, holdings, debts, ages, your state and tax facts, with dates, categories and aliases such as "Account 1", and how each account's data arrives: a bank feed, statements, files or typed in. That is how it answers. An age is sent where a date of birth isn't, so with today's date it gives the year of birth to within one. Steward's promise is about who you are, not about hiding the numbers: your notes and account numbers stay on your machines, the names Steward holds are replaced except in store rows a manager approves, and the checks look for the other details that identify you or your family.

Every claim here links to a row in [the claims ledger](../CLAIMS.md), which names the test or the code that backs it. [How Ask stays private](ask.md) follows one question from the keyboard to the answer.

## What reaches Claude

- **The question you type**, after Steward and the gateway have checked it. The checks look for names, account numbers, email addresses, phone numbers, street addresses and ID numbers, and replace what they find. [P4](../CLAIMS.md#p4) [P5](../CLAIMS.md#p5) [P11](../CLAIMS.md#p11) The page shows what Claude will see as you type. [P16](../CLAIMS.md#p16)
- **Typed answers to Claude's lookups.** Claude asks Steward for data through tools. Each tool returns fields of a fixed type, such as amounts, dates, categories, aliases and fund symbols. It never returns your notes. [P3](../CLAIMS.md#p3)
- **Store and payee names, only in rows a manager approves.** Without that approval Claude gets an alias. A row whose bank line Steward reads as Zelle, a wire, a transfer or a payment app such as Venmo goes as "Zelle to [name]", "Transfer from [name]" and so on, unless the manager also ticks that row after seeing the name on the card. [Which store rows hide a name](#which-store-rows-hide-a-name) lists the words Steward reads. Approved rows stay in that conversation and go with its later questions; a new conversation starts without them. [P6](../CLAIMS.md#p6)

Each part is recorded before the gateway may send it, and a part that can't be recorded is never sent. You can read every part of your data that was sent in Settings › Chat, under Everything sent so far. [P7](../CLAIMS.md#p7) Each request also carries the gateway's fixed instructions and tool list, which hold no household data and are the same for every install of a version. The log records them as a fingerprint on a Getting ready row, and opening that row shows their full text with the fingerprint beside it. The request's settings aren't listed, and a conversation's earlier messages, Claude's replies among them, show as a count. Every member of the book can read the log, and no route in Steward changes or deletes it. [P17](../CLAIMS.md#p17)

## What never reaches Claude

- The names Steward holds, except in store rows a manager approves and in the gaps listed under [What the checks can miss](#what-the-checks-can-miss). In those rows, a row Steward reads as Zelle, a wire, a transfer or a payment app goes as [name] unless the manager ticks that row. A model on your gateway looks for other names and can miss one. [P4](../CLAIMS.md#p4)
- Your notes on transactions and accounts. [P3](../CLAIMS.md#p3)
- Account numbers. [P5](../CLAIMS.md#p5)
- The email addresses, phone numbers, street addresses and ID numbers Steward holds, apart from those gaps. Rules and a model on your gateway look for others and can miss one. [P11](../CLAIMS.md#p11)
- The link that connects Steward to SimpleFIN. None of the code behind Ask, its checks or the gateway reads it. [P13](../CLAIMS.md#p13)
- PDF statements and the summary of a saved plan. [P8](../CLAIMS.md#p8)
- Imported files, their names, and where a row came from. [P12](../CLAIMS.md#p12)

## What stays on your machines

- Steward connects only to the services you set up: SimpleFIN, your gateway, your categorizer, your mail server and your backup ping. [P1](../CLAIMS.md#p1)
- Steward's own code and pages send no telemetry: no analytics, crash reports or outside scripts. [P2](../CLAIMS.md#p2)
- A page for one account or item is titled with its name, never its numbers, and that title appears in your browser's history and, if browser sync is on, with your browser's maker.
- Steward never connects to Claude itself. Only your gateway does. [P14](../CLAIMS.md#p14)
- The gateway is a program on your own hardware. It sees your question after Steward has made its exact replacements, and it runs its own checks before anything leaves.
- Categorizing is local. It uses your gateway's model, or your rules and Review. In Docker Compose the local model has no way out to the internet, and its cloud models are off. [P9](../CLAIMS.md#p9)
- Logs carry no amounts, notes, transaction descriptions or searches. Steward logs a request as its method, its route and its status. Neither Steward nor its HTTPS proxy logs a query string. [P10](../CLAIMS.md#p10) The proxy's error lines leave out the address a request asked for, but its certificate lines name your install's address, such as your domain.
- Emails Steward sends never show an account number. [P15](../CLAIMS.md#p15)

`compose.yml` can also run other programs beside Steward. This is what each of them contacts:

- **Tailscale** (the tailscale profile) talks to Tailscale's servers to connect your devices. Its own log upload to Tailscale is turned off in `compose.yml`, so Tailscale's support can't read those logs. Its HTTPS certificate puts your machine's tailnet name in public certificate logs.
- **The HTTPS proxy** (the domain profile) gets its certificate from Let's Encrypt, which puts your domain in public certificate logs. With `STEWARD_TLS=dns-cloudflare` it also calls Cloudflare's API and asks Cloudflare's DNS at 1.1.1.1. The lan profile's proxy makes its own certificate and contacts nothing.
- **Ollama** (the ollama profile) reads transaction descriptions, statement pages and the questions it turns into chat titles, for the gateway. It sits on a network with no way out, so it contacts nothing, whether idle or categorizing. Its cloud models are turned off, and with them the model recommendations it would otherwise fetch from ollama.com. A model is downloaded from Ollama's library by `ollama-pull`, a second Ollama that runs once on the gateway's way out and never joins the network Steward and the gateway share. [P9](../CLAIMS.md#p9)
- **The gateway** downloads its redaction model from Hugging Face at its first start, pinned to one commit, with Hugging Face's telemetry turned off. [P2](../CLAIMS.md#p2)

## The Sample book

The Sample book is made up. It never syncs with a bank, and nothing from it reaches Claude. Asking is refused in it before any call to the gateway. [S11](../CLAIMS.md#s11)

## What Steward replaces

Before a question leaves Steward, it replaces these by exact match:

- The names of the book's members, children and contacts, and in a parent's book the parent's, in full and each part of 2 letters or more, such as "Li". Each member's username and email too.
- Dates of birth saved in Profile, in the usual written forms.
- Account names, and the banks, whether Steward connected to them or you added them by hand.
- The account numbers, phone numbers, emails and addresses saved in the inventory. An address is also matched when typed short, as its house number and the start of the street, such as "1234 North Elm" or "1234 Elm".
- Every payee the book has seen, the companies that pay you back for work expenses, set-asides, and the nicknames you give rows and rules, each as a whole.

A person's name made only of words that are also months, states or money words is matched too, as the whole name. When that is one word, such as a child called August, Steward can't tell the name from the month, so it replaces the word wherever it appears in your question, and the page shows that before you send. A name part of 2 letters that is also a common word, such as "Do" or "No", is replaced the same way, so "How do we save?" can name that person instead. Everything else you type is left to the gateway's rules and model. [P4](../CLAIMS.md#p4) [P11](../CLAIMS.md#p11)

## Which store rows hide a name

In store rows a manager approves, a row goes as "Zelle to [name]", "Wire from [name]", "Transfer to [name]" and so on, unless the manager ticks it, when Steward finds one of the following. A row with money out says "to" and a row with money in says "from". A payee asked for by its alias says "to" or "from" when all its rows go one way, and "to or from" when they go both ways. Steward reads the row's bank line, its payee and any name Review gave it, in capitals. When a row goes under a name its bank line doesn't hold, such as one Review gave it, Steward also reads every bank line it holds for that payee, and a payee asked for by its alias is read on all of its bank lines. Punctuation is read as spaces, except a star and the punctuation after a name field.

- **Zelle, a wire, bill pay or a numbered check** with a name after "to", such as "CHECK 1042 TO JOHN DOE", or a payment app's name and a star before the person, such as "PAYPAL *JOHNDOE".
- **A payment app:** a word that starts with "zelle", "venmo", "paypal", "pypl", "cashapp", "applecash", "quickpay", "popmoney", "moneygram", "remitly", "xoom" or "worldremit"; the words "cash app", "sqc", "square cash", "apple cash", "apl cash", "zel", "google pay", "gpay", "pay anyone", "western union", "world remit", "wise us", "wise inc" or "wise payments"; or "chime", "wu" or "wise" right before a star.
- **A wire:** a word that ends in "wire", "wired", "wires", "wireout", "wirein" or "wiretrn", such as "fedwire", except inside "hotwire", "livewire", "barbwire", "wired magazine", "wired cafe" or "wired coffee".
- **A transfer word:** a word that contains "transf" or "trnsf"; a word that starts with "xfer" or "xfr"; or the words "tfr", "tfrs", "trf", "trfs", "trsf", "etfr", "send", "sent", "sends", "sender", "sending", "p2p", "rtp", "fednow", "interac emt", "instant payment", "instant pmt" or "real time payment"; except inside "transform", "transfiguration", "transfusion" or "transfer station".
- **A name field** right before ":", "=" or "/": "name", "bnf", "bene", "orig", "rcvr", "receiver", "recipient" or "payee", except inside "ind name"; or the words "beneficiary" or "originator" before a word.
- **A payment or deposit word** with "to" or "from" and a word or number somewhere after it: "payment", "payments", "paymnt", "paymt", "pymt", "pmnt", "pmt", "pyt", "pay", "paid", "ach", "eft", "deposit", "deposits", "dep", "received", "rcvd", "recd", "check", "chk" or "cheque"; or "credit", "cr" or "withdrawal" right before "to" or "from" and a word or number; except inside "check card", "chk card", "checkcard", "credit card" or "statement credit", or when the word after "to" or "from" is "card", "savings", "checking", "account", "return" or "offer".

A store that happens to match, such as one paid through PayPal ("PAYPAL *STEAMGAMES"), is hidden too, and the manager can tick it. Every other row keeps the name from its bank line or from Review, apart from what your gateway finds. After the approval, your gateway reads every row the manager didn't tick and replaces each person's name its model finds with [name]. A row such as "John Doe Plumbing" can go as "[name] Plumbing". The model can also take a store named after someone for a person and replace that name too. A ticked row goes as the manager saw it, and the book's log shows each row as it was sent. Bank of America writes the account holder's own name after "INDN:" and Chase after "IND NAME:" on every ACH line, so those aren't read as name fields. [P4](../CLAIMS.md#p4) [P6](../CLAIMS.md#p6)

## What the checks can miss

The checks are rules and a model, and neither is perfect. These are the cases they can miss.

- A bare date with no birth word, such as "June 14, 1985". With a word like born or birthday beside it, the date is replaced. [P5](../CLAIMS.md#p5)
- An ID made of letters and 4 or fewer digits, such as `AB12CD34`. Steward lets up to 4 digits through, because ages, years and small counts need them. [P5](../CLAIMS.md#p5)
- An email written out in words, such as "tom at example dot com".
- A full name the book doesn't know, when the model doesn't recognize it.
- A name the book doesn't hold that is made of words that are also states, months or money words, such as "Virginia Roth". On its own, "June" could be a month or a person.
- An address the book holds, typed another way, such as "Elm St" for "Elm Street", or without its house number.
- Part of a name that [What Steward replaces](#what-steward-replaces) matches only as a whole, such as "Priya" alone for a payee "Priya Nair" paid by Zelle. Save the person as a contact in the inventory and each part of their name is replaced too.
- Employers, schools, towns and other organizations you type. The model looks only for people, street addresses and dates of birth.
- A person's name in store rows a manager approves, in a row that matches none of the rules under [Which store rows hide a name](#which-store-rows-hide-a-name), when the gateway's model doesn't recognize it: a store that carries its owner's name, a person paid through an app Steward doesn't know, a person named in Review on a deposit or cash row, or a transfer whose bank line uses none of the words listed there. These rows have no tick, so a name the model does find in one always goes as [name]. In a row the rules do hide, the name goes when the manager ticks that row. [P4](../CLAIMS.md#p4) [P6](../CLAIMS.md#p6)
- Anything you type into Claude Code. It goes to Anthropic without passing through Steward. See [Claude Code](../guides/gateway.md#advanced-claude-code).
- Digits typed on purpose in a way meant to get past the rules.

Don't type identifiers into Ask, even ones Steward holds: the exact pass covers only the list under [What Steward replaces](#what-steward-replaces), and the gateway's model and rules can miss the rest. [P4](../CLAIMS.md#p4) [P11](../CLAIMS.md#p11)

## Claude's own terms

What Anthropic does with the questions it receives is set by Anthropic's terms, not by Steward. Read the [commercial terms](https://www.anthropic.com/legal/commercial-terms) and the [privacy center](https://privacy.claude.com) for how your API key's traffic is handled.
