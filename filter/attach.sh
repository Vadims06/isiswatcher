#!/bin/sh
# Builds the filter for this watcher's identity and attaches it to the egress of its tunnel.
set -eu

: "${WATCHER_SYSTEM_ID:?}" "${TUNNEL_NETWORK:?}" "${TUNNEL_PREFIX_LEN:?}"
interface=${FILTER_INTERFACE:-gre1}
object=/tmp/isis_filter.o
log=/tmp/attach.log

# Another node creates the tunnel; an absent one must fail the deploy, not leave the watcher unfiltered
for _ in $(seq 30); do
    ip link show dev "$interface" >/dev/null 2>&1 && break
    sleep 1
done
ip link show dev "$interface" >/dev/null

system_id_bytes=$(echo "$WATCHER_SYSTEM_ID" | tr -d . | sed 's/../0x&,/g; s/,$//')
IFS=. read -r octet1 octet2 octet3 octet4 <<EOT
$TUNNEL_NETWORK
EOT
tunnel_network=$(printf '0x%02x%02x%02x%02xU' "$octet1" "$octet2" "$octet3" "$octet4")

clang -O2 -target bpf -c /opt/isis-filter/isis_filter.c -o "$object" \
    -DWATCHER_SYSTEM_ID_BYTES="$system_id_bytes" -DTUNNEL_NETWORK="$tunnel_network" -DTUNNEL_PREFIX_LEN="$TUNNEL_PREFIX_LEN"
# tc warns about a missing bpf filesystem, which the filter does not need
{
    tc qdisc replace dev "$interface" clsact &&
    tc filter replace dev "$interface" egress pref 1 handle 1 bpf direct-action obj "$object" sec classifier
} 2>"$log" || { cat "$log" >&2; exit 1; }
# Up only now, so no packet leaves the tunnel before the filter is on it
ip link set up dev "$interface"
