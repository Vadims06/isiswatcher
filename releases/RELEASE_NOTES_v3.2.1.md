# IS-IS Watcher Release Notes v3.2.1

Bugfix release. The watcher image behaviour is unchanged - this release fixes
the log-shipping and lab configuration files shipped in this repository.

## Fixes

**Logstash no longer fails to start**
The Logstash pipeline aborted at startup with a fatal configuration error even
when Loki export was switched off. Logstash validates every output in the
pipeline before any condition is evaluated, so the disabled Loki section still
had to be valid - and it was not: the Loki endpoint had no default value, and
the output declared settings the Loki plugin does not accept. The plugin
version is now pinned as well, so a future plugin release cannot break the
pipeline again.

Symptom this fixes: Logstash exits right after start and no events reach the
dashboard, while the watcher log file keeps filling up normally.

**Loki stream labels**
Events pushed to Loki now carry `job`, `event_name`, `event_status`,
`area_num`, `asn` and `watcher_name` as stream labels, matching what Fluent Bit
sends, so the same Grafana queries work with either shipper. See *Push IS-IS
topology changes to Loki* in the README.

**Listen-only (XDP) filter node no longer restarts in a loop**
The lab node that installs the XDP filter does its work once and exits. It was
being restarted continuously - harmless, but indistinguishable from a crash
loop when reading `docker ps`.

## Documentation

- Quick start section at the top of the README.
- Loki stream labels documented.

## Compatibility

| Component | Minimum version |
|---|---|
| Topolograph | v2.66 |
| IS-IS Watcher image (`vadims06/isis-watcher`) | v3.2.0 |

Unchanged from v3.2.0 - this release introduces no new cross-product features.
