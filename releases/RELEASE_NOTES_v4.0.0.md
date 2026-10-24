# IS-IS Watcher Release Notes v4.0.0

## Changes

**The watcher is rewritten in Rust**
The image `vadims06/isis-watcher` is now one static binary with no Python in it. The event log lines and what Topolograph receives stay the same.
* `diagnostic` is built in: `sudo onboarding/diagnostic.sh <num>` checks GRE and IS-IS hellos from both ends, MTU, stale conntrack entries and the iptables rules on the watcher's path, and prints the command that fixes a wrong rule.
* Without Topolograph the watcher says once at start that events go to the local log only.
* IS-IS: no `abr`/`asbr` node events any more. RFC 7752 defines those node flag bits for OSPF only.
* IS-IS: a DIS change no longer reports every router of the LAN as down and up.

**Install from Topolograph**
Topolograph Enterprise's Watchers page now installs a watcher in GRE or BGP-LS mode. It
gives a `configure.sh` that checks the host, builds every watcher of the checkout with
the image's `render --answers`, and starts `topolograph-isiswatcher.service`, which
redeploys the watchers at boot and starts Fluent Bit.
* Each watcher sends its events with its own token.
* Images are pinned to the checkout's `VERSION`; `REGISTRY_PREFIX` points them at a mirror.
* The interactive `client.py` dialog and the Python in the image are gone. Without Topolograph, write an answers file and run `render --answers`, see *Generate configuration files* in `docs/exporters.md`. `print-images` lists the pinned images.

**The announcement filter replaces the XDP filter**
The listen-only filter is now a tc-BPF program on the egress of `gre1`, inside the
watcher's FRR network namespace. It lets out hellos and sequence-number PDUs and, of
all LSPs, only the watcher's own one with the tunnel subnet as its prefix. Before, a
parse failure let a packet through, other systems' LSPs were not checked, and GRE
headers with a key or sequence number were not understood. Now any LSP that cannot
be proven to be the watcher's own is dropped, and drops are counted per reason.

- Image: `vadims06/isis-filter` (was `vadims06/isis-filter-xdp`), pinned as `v1`.
- The filter is switched with `is_filter_enabled` in the answers, then `render` again (`enable_xdp` and `disable_xdp` are gone).
- Answers: `is_filter_enabled`, off by default.
- The filter node runs in the FRR network namespace instead of host networking, and brings `gre1` up itself once attached.
- Needs Linux 5.4 or newer. See *Listen-only mode* in the README for the counters.

## Upgrade

A watcher installed by hand moves to the Topolograph install as described in the README, *Moving a watcher installed by hand*.

Rebuild the watchers with `./configure.sh`. A watcher that ran the XDP filter keeps it
attached to its host veth until its lab is redeployed.

Every watcher restarts once after the upgrade, because its pinned image version changes. The Python images up to `v3.2.0` stay on Docker Hub.
