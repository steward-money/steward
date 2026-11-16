# The gateway for Docker Compose (docs/guides/gateway.md): CPU only, so no NVIDIA wheel is in the image. Its bases are
# the app image's, pinned by digest; tests/test_gateway_image.py checks them, the CPU torch and the user.
FROM python:3.15-slim@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928 AS base
RUN useradd -u 1000 -m steward-gw \
 && install -d -m 700 -o steward-gw -g steward-gw /etc/steward-gateway /var/lib/steward-gateway /var/cache/steward-gateway
COPY --from=ghcr.io/astral-sh/uv:0.9@sha256:538e0b39736e7feae937a65983e49d2ab75e1559d35041f9878b7b7e51de91e4 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY deploy/gateway/torch-cpu.txt ./torch-cpu.txt
COPY src ./src
# Everything uv.lock pins for the gateway and its models but torch and the CUDA wheels PyPI's torch brings, then torch's
# CPU build from PyTorch's own index, pinned by hash. The last checks fail the build if CUDA got in anyway.
RUN uv sync --frozen --no-dev --group gateway --group models --no-cache \
      --no-install-package torch --no-install-package triton \
      --no-install-package cuda-bindings --no-install-package cuda-pathfinder --no-install-package cuda-toolkit \
      --no-install-package nvidia-cublas --no-install-package nvidia-cuda-cupti --no-install-package nvidia-cuda-nvrtc \
      --no-install-package nvidia-cuda-runtime --no-install-package nvidia-cudnn-cu13 --no-install-package nvidia-cufft \
      --no-install-package nvidia-cufile --no-install-package nvidia-curand --no-install-package nvidia-cusolver \
      --no-install-package nvidia-cusparse --no-install-package nvidia-cusparselt-cu13 \
      --no-install-package nvidia-nccl-cu13 --no-install-package nvidia-nvjitlink \
      --no-install-package nvidia-nvshmem-cu13 --no-install-package nvidia-nvtx \
 && uv pip install --python /app/.venv/bin/python --index-url https://download.pytorch.org/whl/cpu \
      --no-deps --require-hashes -r torch-cpu.txt --no-cache \
 && /app/.venv/bin/python -c "import sys, torch; sys.exit(torch.version.cuda is not None)" \
 && ! uv pip list --python /app/.venv/bin/python --no-cache | grep -Eiq '^(nvidia|cuda|triton)' \
 && /app/.venv/bin/python -c "import gliner, presidio_analyzer, anthropic, steward_gateway.app"
COPY --chown=steward-gw:steward-gw deploy/gateway/gateway.toml /etc/steward-gateway/gateway.toml
ENV STEWARD_GATEWAY_CONFIG_DIR=/etc/steward-gateway STEWARD_GATEWAY_STATE_DIR=/var/lib/steward-gateway HF_HOME=/var/cache/steward-gateway XDG_CACHE_HOME=/var/cache/steward-gateway HF_HUB_DISABLE_TELEMETRY=1 PATH="/app/.venv/bin:$PATH"
USER steward-gw
WORKDIR /var/lib/steward-gateway
VOLUME ["/etc/steward-gateway", "/var/lib/steward-gateway", "/var/cache/steward-gateway"]
EXPOSE 8430
CMD ["steward-gateway", "serve"]
