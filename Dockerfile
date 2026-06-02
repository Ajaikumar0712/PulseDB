# ── Stage 1: Builder ──────────────────────────────────────────────────────
FROM rust:1.85-slim AS builder

# Install build dependencies (OpenSSL headers needed by some crates)
RUN apt-get update && apt-get install -y --no-install-recommends \
    pkg-config \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Cache dependency compilation separately from source
COPY Cargo.toml Cargo.lock ./
# Create dummy main so cargo can fetch and compile deps
RUN mkdir -p src && echo 'fn main(){}' > src/main.rs && echo 'fn main(){}' > src/repl.rs
RUN cargo build --release --bin pulsedb-server 2>/dev/null || true

# Now copy real source and build
COPY src ./src
# Touch src files so Cargo sees them as newer than the dummy build
RUN touch src/main.rs src/repl.rs
RUN cargo build --release --bin pulsedb-server --bin pulsedb-repl

# ── Stage 2: Runtime ──────────────────────────────────────────────────────
FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    netcat-openbsd \
    && rm -rf /var/lib/apt/lists/*

# Create a non-root user for the database process
RUN useradd -r -s /bin/false -m -d /var/lib/pulsedb pulsedb

COPY --from=builder /build/target/release/pulsedb-server  /usr/local/bin/pulsedb-server
COPY --from=builder /build/target/release/pulsedb-repl    /usr/local/bin/pulsedb-repl

# Data directory (mount a volume here for persistence)
RUN mkdir -p /var/lib/pulsedb && chown pulsedb:pulsedb /var/lib/pulsedb

USER pulsedb
WORKDIR /var/lib/pulsedb

# Server port
EXPOSE 7878

# Default: listen on all interfaces so Docker port-mapping works
CMD ["pulsedb-server", "--addr", "0.0.0.0:7878", "--data-dir", "/var/lib/pulsedb"]
