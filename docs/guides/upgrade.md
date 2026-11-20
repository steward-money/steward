# Upgrade Steward

Read this when a new version is out and you want to move to it. Each version's notes on the [Releases page](https://github.com/steward-money/steward/releases) say what changed and anything to do before you upgrade.

1. Back up first.

   ```
   docker compose exec steward steward backup
   ```

2. Set the new version in `compose.yml`, in the `image:` lines of `steward`, and of `steward-proxy` and `steward-gateway` if you use them. If you use a GPU gateway, run `git pull` in the clone instead: it brings the new `compose.yml`, and the source the GPU gateway is built from. If git stops because you changed `compose.yml`, move your changes to `compose.override.yml`, run `git checkout compose.yml`, and pull again. Then rebuild the GPU gateway with the command for your GPU.

   ```
   docker compose --profile gateway-nvidia build
   docker compose --profile gateway-amd build
   ```

3. If you pin digests in `compose.override.yml`, verify the new images and put their digests there (see [Verify the images](#verify-the-images)). That file decides what is pulled, so a version set only in `compose.yml` changes nothing.
4. Pull and restart.

   ```
   docker compose pull && docker compose up -d
   ```

Migrations run when Steward starts, so there is nothing else to run. Check `docker compose ps` shows Steward as healthy, then open it.

A migration can change a book in a way the old version cannot read. If a new version fails to start, restore the backup from step 1 before you go back to the old version (see [backups](backups.md)).

## Verify the images

Each release image is signed by the release workflow, with no stored key, and comes with an SBOM. [S8](../CLAIMS.md#s8) To check one before you run it, install cosign 3 or newer, and jq. Cosign 2 can't read these signatures and reports "no signatures found". Run this with the version you are pulling.

```
cosign verify ghcr.io/steward-money/steward:<version> \
  --certificate-identity-regexp '^https://github\.com/steward-money/steward/\.github/workflows/release\.yml@refs/tags/v' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  | jq -r '.[0].critical.image."docker-manifest-digest"'
```

It prints a digest only when the image was built by that workflow from a version tag, such as `sha256:9f726fa2a4725b80578b4b4262d6bf4cc18d20a2df394eb8e4a65ab1c4d8f290` for 0.1.0. If the check fails, cosign prints an error and you get no digest. No digest means the image failed the check. Don't go by the exit status, which is jq's and is 0 either way. In a script, run `set -eo pipefail` first, so a failed check stops the script. Use the same command with `steward-proxy` or `steward-gateway` in place of `steward`.

A tag can be moved to another image after you check it, so pull the digest you checked rather than the tag. Make `compose.override.yml` beside `compose.yml`, with each image's own digest. Keep the services you use and delete the rest.

```
services:
  steward:
    image: ghcr.io/steward-money/steward:<version>@<steward's digest>
  proxy:
    image: ghcr.io/steward-money/steward-proxy:<version>@<steward-proxy's digest>
  proxy-lan:
    image: ghcr.io/steward-money/steward-proxy:<version>@<steward-proxy's digest>
  gateway:
    image: ghcr.io/steward-money/steward-gateway:<version>@<steward-gateway's digest>
```

Docker Compose reads this file along with `compose.yml`, and pulls each image by its digest. Docker refuses an image whose content doesn't match the digest.

A GPU gateway is built on your machine instead, from the source in your clone (see [the GPU guide](gpu.md)). `git pull` in step 2 is what moves it to a new release.

See [ports and volumes](../reference/ports-and-volumes.md) for what an upgrade leaves alone.
