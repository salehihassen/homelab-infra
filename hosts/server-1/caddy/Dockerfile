FROM caddy:2.11.4-builder-alpine AS builder

RUN xcaddy build v2.11.4 \
    --with github.com/caddy-dns/porkbun@v0.3.1

FROM caddy:2.11.4-alpine

COPY --from=builder /usr/bin/caddy /usr/bin/caddy

# Preserve only the low-port binding capability across the routing shell and
# Caddy exec. A dedicated shell leaves the system BusyBox binary unprivileged;
# no-new-privileges can remain enabled because Caddy gains no new capability.
RUN mkdir -p /usr/local/libexec/caddy \
    && cp /bin/busybox /usr/local/libexec/caddy/sh \
    && setcap cap_net_bind_service=+ep /usr/local/libexec/caddy/sh \
        cap_net_bind_service=+ep /usr/bin/caddy
