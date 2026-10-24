#!/bin/sh
# Runs every verdict case against the filter attached by attach.sh, in a privileged container.
set -eu
cd "$(dirname "$0")/.."
image=isis-filter-test
docker build -q -f tests/Dockerfile -t "$image" . >/dev/null
docker run --rm --privileged --entrypoint python3 "$image" -I /opt/isis-filter/check_cases.py
