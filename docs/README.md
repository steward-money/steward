# Steward's docs

## Quick starts

- [Try the demo in 5 minutes](quickstart/demo.md): run a made-up household on your machine and look around.
- [Install for your household](quickstart/install.md): the shortest safe path to your own Steward.

## Guides

- [Choose how to reach Steward](guides/access.md): Tailscale, your own domain, or your local network.
- [Set up email](guides/email.md): password reset links, invitations to a book and Watching alerts.
- [Connect your bank or import files](guides/bank-data.md): get transactions into a book.
- [Backups and restore](guides/backups.md): keep copies, check them and bring one back.
- [Keep the encryption key safe](guides/encryption-key.md): where the key lives and how to protect it.
- [Pair the gateway and Ask Claude](guides/gateway.md): turn on Ask and local categorizing.
- [Speed up the gateway with a GPU](guides/gpu.md): build a CUDA or ROCm gateway on your own machine.
- [Upgrade Steward](guides/upgrade.md): move to a new version.
- [Move Steward to a new machine](guides/move.md): carry your books across.

## Reference

- [Commands](reference/commands.md): every `steward` and `steward-gateway` command.
- [Configuration](reference/configuration.md): every setting.
- [Ports, volumes and files](reference/ports-and-volumes.md): what the compose files open, mount and keep.
- [Claims](CLAIMS.md): each promise in these docs and what proves it.

## Explanations

- [Architecture](explanations/architecture.md): the pieces and how they fit.
- [Privacy](explanations/privacy.md): what Claude sees and never sees.
- [How Ask stays private](explanations/ask.md): one question, followed from keyboard to answer.
- [Threat model](explanations/threat-model.md): what Steward protects, from whom, and where that stops.

## Releases

What changed in each release, what to do before upgrading, and how to check the images. The same notes are on the [Releases page](https://github.com/steward-money/steward/releases).

- [0.1.2](releases/0.1.2.md)
- [0.1.1](releases/0.1.1.md)
- [0.1.0](releases/0.1.0.md)

## Reviews

The 0.1.0 release gate's summaries: what each review checked, what it found, what was fixed and what's left.

- [Security](reviews/0.1.0-security.md)
- [Privacy](reviews/0.1.0-privacy.md)
- [Thoughtfulness](reviews/0.1.0-thoughtfulness.md)

Also in the repository: [SECURITY](../SECURITY.md), [CONTRIBUTING](../CONTRIBUTING.md), [LICENSE](../LICENSE) and [NOTICE](../NOTICE).
