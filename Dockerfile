# VeriSpec official image.
#
#   docker build -t verispec .
#   docker run --rm -v "$PWD:/work" -w /work verispec check specs/bank.verispec.yaml
#
# The image is small, runs as a non-root user and ships only the CLI and its
# dependencies (z3-solver and PyYAML).

FROM python:3.11-slim AS builder

WORKDIR /build
COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m pip install --no-cache-dir --upgrade pip build \
    && python -m pip wheel --no-cache-dir --wheel-dir /wheels .

FROM python:3.11-slim

LABEL org.opencontainers.image.title="verispec" \
      org.opencontainers.image.description="Spec-driven development with formal teeth" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.source="https://github.com/verispec/verispec"

RUN useradd --create-home --uid 10001 verispec
COPY --from=builder /wheels /wheels
RUN python -m pip install --no-cache-dir --no-index --find-links=/wheels verispec \
    && rm -rf /wheels

USER verispec
WORKDIR /work

ENTRYPOINT ["verispec"]
CMD ["--help"]
