# IS-IS Watcher Release Notes v4.1.0

**Macvlan mode**
A third connection mode next to GRE and BGP-LS: the watcher forms an IS-IS adjacency with a router on the same L2 segment, over a physical or VLAN interface of the host. No tunnel and no iptables rules. The announcement filter is always on in this mode. See [Macvlan mode](../README.md#macvlan-mode) for the limits.

**Announcement filter v2.0.0**
The filter works on Ethernet as well as GRE, and drops any IS-IS packet it cannot read.

**The watcher never sets the ATT bit**
The watcher's FRR runs with `no attached-bit send`, and the announcement filter drops any LSP with the ATT bit, so level-1 routers never send default traffic to the watcher.

## Upgrade
Run `configure.sh` again with the command from the watcher page. Every watcher restarts once.
