# IS-IS Watcher Release Notes v3.2.0

> [!IMPORTANT]
> **Before pulling this version, update your Logstash and Fluent Bit schemas.**
> This release adds a new **SRLG** field to IS-IS link-attribute events. The
> watcher image (`vadims06/isis-watcher:v3.2.0`) emits the new field, so the
> pipeline that parses watcher logs must be updated **at the same time**, or
> events carrying SRLG will fail to parse / drop the field.
>
> Pull the matching schema files from this repo together with the image:
> - `logstash/` — pipeline filters and index templates
> - `fluentbit/` — `fluent-bit.yaml` parser/pipeline
>
> Recommended order: stop the stack → pull the new image **and** the updated
> `logstash/` + `fluentbit/` files → start the stack.

## New features

**SRLG (Shared Risk Link Group) visualization and tracking**
The watcher now reads SRLG membership from the IS-IS control plane and
tracks changes over time:
- **IS-IS** — IPv4 SRLG (RFC 5307, TLV 138) and IPv6 SRLG (RFC 6119, TLV 139),
  in both GRE and BGP-LS modes.
- SRLG **added / removed / changed** on a link is reported through the existing
  **link-attribute change event** (alongside metric / TE metric), so no new
  event type is introduced — existing alerting keys continue to work once the
  pipeline schema is updated.

## Version → features

| Version | New features |
|---------|--------------|
| v3.0.0  | BGP-LS mode — receive IS-IS topology via BGP-LS (GoBGP forwarder) |
| v3.0.1  | BGP-LS multihop TTL; GoBGP runs inside the watcher namespace |
| v3.1.0  | Logstash parses session/source id on IS-IS event lines; delimiter-based metric / TE-metric filters |
| v3.1.x  | Fluent Bit profile and pipeline; watcher heartbeat compatible with Topolograph 2.63 |
| v3.1.5  | Loki output for Logstash and Fluent Bit; node-change (overload / attached / ABR / ASBR) event parsing |
| v3.1.6  | IS-IS node-flag baseline seeded from the initial LSDB (overload / attached) |
| **v3.2.0** | **SRLG visualization & tracking — IS-IS (RFC 5307 TLV 138 / RFC 6119 TLV 139), GRE + BGP-LS; SRLG changes folded into the link-attribute event. Requires updated Logstash + Fluent Bit schemas.** |
