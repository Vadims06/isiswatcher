# IS-IS Watcher Release Notes v3.4.0

## Changes

**Install from Topolograph**
Topolograph Enterprise's Watchers page now installs a watcher in GRE or BGP-LS mode. It
gives a `configure.sh` that checks the host, builds every watcher of the checkout with
`client.py --action add_watcher --answers`, and starts `topolograph-isiswatcher.service`, which
redeploys the watchers at boot and starts Fluent Bit.
* Each watcher sends its events with its own token.
* Images are pinned to the checkout's `VERSION`; `REGISTRY_PREFIX` points them at a mirror.
* The interactive `client.py` dialog is unchanged.

**The announcement filter replaces the XDP filter**
The listen-only filter is now a tc-BPF program on the egress of `gre1`, inside the
watcher's FRR network namespace. It lets out hellos and sequence-number PDUs and, of
all LSPs, only the watcher's own one with the tunnel subnet as its prefix. Before, a
parse failure let a packet through, other systems' LSPs were not checked, and GRE
headers with a key or sequence number were not understood. Now any LSP that cannot
be proven to be the watcher's own is dropped, and drops are counted per reason.

- Image: `vadims06/isis-filter` (was `vadims06/isis-filter-xdp`), pinned as `v1`.
- Actions: `enable_filter` and `disable_filter` (were `enable_xdp` and `disable_xdp`).
- Answers: `is_filter_enabled`, off by default.
- The filter node runs in the FRR network namespace instead of host networking, and brings `gre1` up itself once attached.
- Needs Linux 5.4 or newer. See *Listen-only mode* in the README for the counters.

## Upgrade

A watcher installed by hand moves to the Topolograph install as described in the README, *Moving a watcher installed by hand*.

Rebuild the watchers with `./configure.sh`. A watcher that ran the XDP filter keeps it
attached to its host veth until its lab is redeployed.
