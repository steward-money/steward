# Steward

**Steward: secure, private, thoughtful. For you, and the people you look after.**

Ask AI about your money without handing it your money.

<p align="center">
<img src="docs/screenshots/home.png" width="230" alt="Home: the month so far, with income, spending and what needs a look">&nbsp;&nbsp;&nbsp;&nbsp;
<img src="docs/screenshots/review.png" width="230" alt="Review: one transaction at a time, sorted with a tap">&nbsp;&nbsp;&nbsp;&nbsp;
<img src="docs/screenshots/ask.png" width="230" alt="Ask: a plain question about spending, answered from your own books">
</p>

<img src="docs/screenshots/plan.png" width="720" alt="Plan: when you can retire, and what would move it">

Steward is a self-hosted household finance app: spending, accounts, Review, a retirement plan, taxes and Ask, for you and the people you look after.

Some AI money tools link your bank and let the model read your raw transactions. Steward is software you run on your own machine. Before a question goes to Claude, Steward and your gateway check it, and [Privacy](docs/explanations/privacy.md) lists what Claude sees.

## Get started

- [Try the demo in 5 minutes](docs/quickstart/demo.md)
- [Install for your household](docs/quickstart/install.md)
- [All the docs](docs/README.md)

## Secure, Private, Thoughtful

Each promise links to its proof in the [claims ledger](docs/CLAIMS.md).

### Secure

- Your books are encrypted at rest with SQLCipher, using AES-256. [S1](docs/CLAIMS.md#s1)
- You can keep the key on its own mount, and backups never hold it. [S2](docs/CLAIMS.md#s2)
- PDF statements are opened in a sandbox, never unconfined. [S6](docs/CLAIMS.md#s6)
- There's no open sign-up: the first person comes from a one-time setup link. [S7](docs/CLAIMS.md#s7)

### Private

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/images/privacy-flow-dark.svg">
  <img src="docs/images/privacy-flow-light.svg" width="880" alt="Your question goes from Steward through your gateway to Claude. Account and payee names become aliases such as Account 1 and Payee 4, notes never leave your machine, and figures such as spending by category go through as they are. The answer comes back in aliases, and Steward puts the real names back.">
</picture>

- Steward never connects to Claude itself; only your gateway does. [P14](docs/CLAIMS.md#p14)
- Claude never receives the names Steward holds, except in store rows a manager approves and in the gaps [Privacy](docs/explanations/privacy.md#what-the-checks-can-miss) lists. A model on your gateway looks for other names and can miss one. [P4](docs/CLAIMS.md#p4)
- Claude never receives your notes. [P3](docs/CLAIMS.md#p3)
- Claude never receives account numbers. [P5](docs/CLAIMS.md#p5)
- You can see every part of your data that was sent. [P7](docs/CLAIMS.md#p7)
- Steward connects only to the services you set up: SimpleFIN, your gateway, your categorizer, your mail server and your backup ping. [P1](docs/CLAIMS.md#p1)

Claude does see the figures it needs: balances, income, spending totals, holdings, debts, ages, your state and tax facts, with dates, categories and aliases such as "Account 1". That is how it answers questions about money. Store and payee names reach Claude only in rows a manager approves. In those rows, a row whose bank line Steward reads as Zelle, a wire, a transfer or a payment app goes as [name] unless the manager ticks that row. [P6](docs/CLAIMS.md#p6) [Privacy](docs/explanations/privacy.md#which-store-rows-hide-a-name) lists the words Steward reads and what the checks can miss.

### Thoughtful

- Review sorts, Snooze, Ignore, notes, and Profile and spending changes can be undone for 15 minutes. [T2](docs/CLAIMS.md#t2)
- Every delete of something you've saved has an Undo or a clear confirm. [T3](docs/CLAIMS.md#t3)
- Steward stays quiet about money worries: no cheers on bad news. [T6](docs/CLAIMS.md#t6)
- Works in dark mode and on phones. [T8](docs/CLAIMS.md#t8)
- Nothing vanishes unseen. [T1](docs/CLAIMS.md#t1)
- Every screen was walked on a phone in dark mode before release. [T5](docs/CLAIMS.md#t5)
- Every screen passes automated accessibility checks (axe, with its WCAG A and AA rules up to WCAG 2.2), on a phone and a laptop, in dark and light. [T7](docs/CLAIMS.md#t7)

For the rest, read the [threat model](docs/explanations/threat-model.md), [Privacy](docs/explanations/privacy.md) and the [claims ledger](docs/CLAIMS.md).

## Scope

Steward 0.1.2 is early. It is for US households of one or two adults, on x86_64 Linux with Docker (Podman isn't supported yet). Transactions come from SimpleFIN or files. The gateway is optional.

## How Steward is made

Steward is designed and directed by its maintainer, JR, and largely written with Claude, Anthropic's AI model. JR reviewed and approved every change, from the architecture and design to how each feature works, and JR's own family runs its money on Steward. Each change is tested, and the 0.1.0 release passed security, privacy and thoughtfulness reviews and a timed install from the public docs. The [release reviews](docs/reviews/) say what they found.

## License

Steward is AGPL-3.0-or-later. A changed copy that you run for others must offer them its source. See [NOTICE](NOTICE), [SECURITY.md](SECURITY.md) and [CONTRIBUTING.md](CONTRIBUTING.md).

## Not advice

Steward's figures are estimates, not financial or tax advice. Steward is not affiliated with Anthropic.
