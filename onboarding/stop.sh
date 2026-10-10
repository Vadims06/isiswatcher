#!/usr/bin/env bash
# Takes every watcher of this checkout down, with the host rules a GRE watcher added:
# containerlab destroy leaves those behind. Run on stop by topolograph-isiswatcher.service.
set -uo pipefail
cd "$(dirname "$0")/.."

status=0
# Found by their comment, so a stop after a Docker crash still removes them
for table in nat filter; do
    while read -r rule; do
        eval iptables -t "$table" "$rule" || status=1
    done < <(iptables -t "$table" -S | grep -- "--comment topolograph-isiswatcher" | sed 's/^-A /-D /')
done
for config in watcher/watcher[0-9]*/config.yml; do
    [ -e "$config" ] || continue
    containerlab destroy -t "$config" || status=1
done
docker compose --profile fluentbit stop isis-fluentbit || status=1
exit $status
