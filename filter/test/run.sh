#!/bin/sh
# Runs every verdict case against the filter attached by attach_filter.sh, in a privileged container.
set -eu
cd "$(dirname "$0")/.."
image=isis-filter-test
docker build -q -f test/Dockerfile -t "$image" . >/dev/null
docker run --rm --privileged --entrypoint python3 ${1:+-v "$PWD/$1":/opt/isis-filter/announcement_filter.c} "$image" -I /opt/isis-filter/check_cases.py
