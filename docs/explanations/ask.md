# How Ask stays private

This page follows one question, "How much did we spend on groceries last month?", from the keyboard to the answer. Each step says where it runs. For the full lists of what Claude sees and never sees, read [privacy](privacy.md).

1. **You type the question in your browser.** The page shows what Claude will see as you type, so you can reword anything before sending. [P16](../CLAIMS.md#p16)

   <img src="../screenshots/ask.png" width="300" alt="The Ask panel on a phone, with a card headed What Claude will see that reads: It's about Eating out in November 2026.">

2. **Steward replaces what it knows.** Steward runs on your machine. It replaces the book's own names, such as people, accounts, institutions and payees, with aliases like "Person 1" and "Account 3" or a placeholder like "[institution]". This is an exact match, so it does not guess, and it covers what [privacy](privacy.md#what-steward-replaces) lists. [P4](../CLAIMS.md#p4) Claude can see a store or payee name in one case, when a manager approves it (step 6). [P6](../CLAIMS.md#p6)
3. **The gateway checks the question again.** The gateway runs on your own hardware, apart from Steward. It runs a model that looks for names, addresses and dates of birth the book does not know, and it compares the text against fingerprints of the names and numbers the book does know. It replaces what it finds. [P4](../CLAIMS.md#p4) [P11](../CLAIMS.md#p11) The checks can miss things; [privacy](privacy.md#what-the-checks-can-miss) lists them.
4. **The gateway sends the question to Claude.** Steward itself never connects to Claude. [P14](../CLAIMS.md#p14)
5. **Claude asks for data.** Claude calls tools, and Steward runs each one on your machine. Each tool returns typed fields, such as amounts, dates, categories and aliases, and never your notes or account numbers. The one name a tool can return is a store or payee name in rows a manager approves (step 6). [P3](../CLAIMS.md#p3) [P4](../CLAIMS.md#p4) [P5](../CLAIMS.md#p5)
6. **Store names wait for a manager.** If Claude needs store or payee names to answer, a manager sees the rows and approves them. A row whose bank line Steward reads as Zelle, a wire, a transfer or a payment app goes as "Zelle to [name]", "Transfer from [name]" and so on, unless the manager ticks that row to send the name too. In rows the manager didn't tick, your gateway also replaces a person's name its model finds with [name]. [Privacy](privacy.md#which-store-rows-hide-a-name) lists the words it reads. Approved rows stay in that conversation and go with its later questions; a new conversation starts without them. Without approval Claude works from the alias. [P6](../CLAIMS.md#p6)
7. **The answer comes back to Steward.** Steward turns the aliases in it back into names, on your machine. Claude saw only the aliases for the names Steward replaced. [P4](../CLAIMS.md#p4)
8. **You can see every part of your data that was sent.** Steward records each part of the turn before the gateway may send it, and a part it can't record is never sent. You can read the log in Settings › Chat, under Everything sent so far. [P7](../CLAIMS.md#p7) Every member of the book can read it, and no route in Steward changes or deletes it. [P17](../CLAIMS.md#p17)

   <img src="../screenshots/sent.png" width="300" alt="The Everything sent so far list on a phone, showing three requests from today, newest first.">


Steward never calls Claude itself. Only your gateway reaches Claude. [P14](../CLAIMS.md#p14)

See [pair the gateway](../guides/gateway.md) to set it up, and [architecture](architecture.md) for where each piece runs.
