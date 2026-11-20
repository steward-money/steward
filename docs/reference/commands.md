# Commands

Steward has two command line programs. `steward` is the app itself, and `steward-gateway` runs the [gateway](../guides/gateway.md) that lets Ask reach Claude.

With Docker, run a `steward` command inside the running container, from the folder that holds `compose.yml`:

```
docker compose exec steward steward <command>
```

Before the first start there is no running container, so use `run --rm` instead:

```
docker compose run --rm steward steward <command>
```

`serve`, `create-user`, `reset-password`, `seed-demo` and `backup` stop with "No database at … Run `steward migrate` first." rather than quietly making an empty one. Only `migrate` and `setup` create the database.

`steward-gateway` commands run where the gateway runs. With the gateway profile in compose, that is `docker compose exec gateway steward-gateway <command>`. See the [gateway guide](../guides/gateway.md).

## steward

### `steward setup`

The first run. Makes the encryption key (at `STEWARD_KEY_FILE` if that is set), sets up the database and prints a one-time link for making the first account. The link works once, for 30 minutes. Running `setup` again closes the old link and prints a new one. It refuses once anyone has an account.

### `steward init-key`

Creates the database encryption key if it doesn't exist yet.

### `steward keygen`

Prints a new key, for `STEWARD_KEY_FILE` (a Docker secret, for example). It writes nothing.

### `steward migrate`

Creates or upgrades the directory and every book. The first run of a release that has books moves the existing sign-ins into the directory, and a backup comes first.

### `steward serve`

Runs the web app on the configured host and port.

### `steward create-user`

`steward create-user USERNAME EMAIL DISPLAY_NAME` adds a person to a book.

- `--book SLUG` is the book to add them to. The default is `ours`.
- `--role manager|viewer` is what they can do there. The default is `manager`.
- `--password` sets the password. Without it, the command asks.

### `steward reset-password`

`steward reset-password USERNAME` sets a new password for that person, removes their passkeys and signs them out everywhere, which also ends their Claude Code grants. It says how many passkeys went. Without `--password`, it asks for one.

Use it when someone is locked out. Five wrong passwords at a username or email from one device lock password sign-in at that name on that device for 15 minutes. 50 in a row from devices that haven't signed in to the account before lock it on every such device for 15 minutes, while a device that has signed in before can still try. A passkey still works during that time. [S5](../CLAIMS.md#s5) If a lockout is the only problem, waiting it out is enough. If the password is lost, run:

```
docker compose exec steward steward reset-password USERNAME
```

### `steward book create`

`steward book create NAME --manager USERNAME` makes a book when nobody can sign in to do it from Settings › Books.

- `--kind household|parent` is the kind of book. The default is `parent`.
- `--owner NAME` is an owner's name, for a parent book. A parent book takes one to eight, and the option repeats.
- `--manager USERNAME` is the first manager. It is required, and it repeats.

A household book always has Person 1, Person 2, Joint and Business.

### `steward book list`

Lists every book with its slug, kind and name, and says why it can't be opened if it can't.

### `steward book people`

`steward book people [SLUG]` lists who is in a book, oldest first, and which two a household calls Person 1 and Person 2. The default book is `ours`. It changes nothing.

### `steward backup`

Writes an encrypted copy of the directory and of every book, and keeps the 14 newest of each. `--dir DIR` chooses where to write. The default is `STEWARD_BACKUP_DIR`, otherwise `backups` in the data folder. It never writes into a folder that holds the key. See [backups](../guides/backups.md).

### `steward restore-check`

Checks that the backups restore. Each copy must open with its key, be whole, current and not empty, and every uploaded file must decrypt. It exits 0 when everything passed and 1 when anything failed. It never writes to the live books.

- `--dir DIR` is the backups folder to check. The default is `STEWARD_BACKUP_DIR`, otherwise `backups` in the data folder.
- `--max-age-hours N` fails the check when the newest backup is older than N hours. The default is 30.

### `steward restore-from-export`

`steward restore-from-export FILE` makes a new book from an encrypted export made in Settings › Export. It asks for the export's passphrase and never overwrites a book: the new one gets a new id and a key from this install's key.

- `--manager USERNAME` is a manager of the new book. It is required, and it repeats.
- `--name NAME` is the new book's name. The default is the old name followed by "(restored)".

### `steward sync`

Pulls the latest accounts and transactions from your banks now, for every book with a link. `--book SLUG` limits it to one book. It exits 1 if a book failed, or if the named book isn't linked. See [bank data](../guides/bank-data.md).

### `steward same-accounts`

Shows how many "Same account?" questions each household book has open. With `--check`, it also looks again for bank accounts already in the book that look like one account reached through two logins; those are asked about on Home.

### `steward seed-demo`

Loads the made-up demo household. Never run it against real data.

- `--years N` is how many years of history to make. The default is 2.
- `--seed S` is the seed. The default is 7, and the same seed makes the same data.
- `--force` seeds alongside whatever is already there.

It refuses to run when `STEWARD_ENV` is `prod`, unless `STEWARD_ALLOW_DEMO=1` is set, and it refuses a database that already has transactions.

### `steward bench`

Times the retirement engine against its budgets on this machine. It needs no database. `--runs N` is the number of timed solves (default 5) and `--histories H` is the simulated histories per solve (default 2000). It exits 1 when a budget is missed.

### `steward ask`

`steward ask QUESTION --book SLUG --as USERNAME` asks Claude about a book through the gateway, as that member. `--conversation ID` continues an earlier conversation.

### `steward advisor pair`

Pairs this install with the gateway. It prints a code to give to `steward-gateway pair` on the gateway's machine, then waits. `--gateway URL` names the gateway when `STEWARD_GATEWAY_URL` isn't set. See [pairing](../guides/gateway.md#pairing).

### `steward advisor check`

Shows whether the gateway answers, and this month's spending.

### `steward advisor log`

Lists what was sent to Claude from a book, newest first. `--book SLUG` is required, `--limit N` sets how many (default 20), and `--json` prints every row with its exact JSON.

## steward-gateway

Each of these is explained in the [gateway guide](../guides/gateway.md).

### `steward-gateway serve`

Runs the gateway.

### `steward-gateway check`

Confirms that the Claude API key works with one request that asks for no answer, and shows this month's spending against the cap. See [the Claude API key](../guides/gateway.md#the-claude-api-key).

### `steward-gateway pair`

Accepts a pairing code for the next 10 minutes. `steward-gateway pair CODE` takes one made by `steward advisor pair`. Without a code, it makes one to type into Steward. See [pairing](../guides/gateway.md#pairing).

### `steward-gateway stop-chat`

Refuses all chat until `start-chat`.

### `steward-gateway start-chat`

Allows chat again after `stop-chat`.

### `steward-gateway pdf-check`

Says whether this machine can open statement PDFs for Steward. See [reading PDFs](../guides/gateway.md#reading-pdfs).

### `steward-gateway hardware`

Says what the gateway runs on now and which GPUs and NPUs this machine has, then names the next step. It only looks and installs nothing. See [Speed up the gateway with a GPU](../guides/gpu.md#find-out-what-you-have).

### `steward-gateway bench`

Times the first answer token over 20 made-up questions through the real Claude API. It spends a little, so it asks first; `--yes` skips the question.

### `steward-gateway eval categorizer`

Runs the categorizer's labelled set and times it. It runs only on this machine and sends nothing out.

- `--model NAME` is an Ollama model to try. It repeats, to compare several. The default is the gateway's `categorizer_model`.
- `--set PATH` is the labelled set to use instead of the one shipped with the gateway.

### `steward-gateway eval statements`

Runs the made-up statements through Steward's PDF reading with this machine's models.

- `--model NAME` is an Ollama text model for new layouts. It repeats. The default is `statement_layout_model`.
- `--vision-model NAME` is an Ollama vision model for scanned pages. It repeats. The default is `statement_vision_model`.

### `steward-gateway eval redaction`

Times redaction with the real models on this machine.

### `steward-gateway eval answers`

Asks 32 made-up questions over the demo books through the real Claude API and checks the figures. It spends a little, so it asks first; `--yes` skips the question.

### `steward-gateway mcp-grant add`

Asks for a grant from Settings › Chat › Claude Code, saves it, and prints the command that adds it to Claude Code. `steward-gateway mcp-grant add GRANT` takes the grant when you already have one.

### `steward-gateway mcp-grant list`

Lists the books Claude Code can look up from this machine.

### `steward-gateway mcp-grant remove`

`steward-gateway mcp-grant remove NAME` forgets a grant on this machine by its name, like `steward-1`. Revoke it in Settings › Chat too.
