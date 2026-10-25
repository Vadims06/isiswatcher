"""Runs crafted IS-IS PDUs through the attached filter with BPF_PROG_TEST_RUN and checks verdict and drop reason.

In production the PDU starts at skb->data of gre1 and the 802.2 LLC header (fe fe 03) starts it on a macvlan;
BPF_PROG_TEST_RUN strips the Ethernet header the same way, so each packet here is Ethernet + PDU.
Every IS-IS case runs in both framings. Test run cannot set skb->protocol: a length-type field gives 802.2,
which stands in for the 0x00fe of gre1 and for the length a macvlan reports.
"""
import json
import os
import re
import struct
import subprocess
import sys
import tempfile

WATCHER_SYSTEM_ID = "0002.1025.1111"
TUNNEL_NETWORK = "10.10.25.0"
TUNNEL_PREFIX_LEN = "24"
INTERFACE = "gre1"
COUNTER_NAMES = ["lsp_passed", "truncated", "unknown_pdu", "foreign_lsp", "malformed_tlv", "neighbor", "prefix", "attached"]
TC_ACT_OK = 0
TC_ACT_SHOT = 2
PASS, DROP = "PASS", "DROP"

WATCHER = bytes.fromhex("000210251111")
DEVICE = bytes.fromhex("000000000002")
OTHER_ROUTER = bytes.fromhex("000000000009")
LSP_L1, LSP_L2, P2P_HELLO, LAN_HELLO_L1, CSNP_L2, PSNP_L2 = 18, 20, 17, 15, 25, 27


def make_tlv(tlv_type, body):
    return bytes([tlv_type, len(body)]) + body


def make_prefix_entry(prefix, prefix_len, sub_tlvs=b""):
    packed = bytes(int(octet) for octet in prefix.split("."))[:(prefix_len + 7) // 8]
    control = prefix_len | (0x40 if sub_tlvs else 0)
    entry = struct.pack(">IB", 10, control) + packed
    return entry + (bytes([len(sub_tlvs)]) + sub_tlvs if sub_tlvs else b"")


def make_is_entry(neighbour=DEVICE, pseudonode=0, sub_tlvs=b""):
    return neighbour + bytes([pseudonode]) + (10).to_bytes(3, "big") + bytes([len(sub_tlvs)]) + sub_tlvs


def make_lsp(tlvs=(), system_id=WATCHER, pseudonode=0, fragment=0, pdu_type=LSP_L2, flags=0,
             pdu_len=None, header_len=27, id_len=0, trailer=b""):
    body = b"".join(tlvs)
    total_len = 27 + len(body) if pdu_len is None else pdu_len
    header = bytes([0x83, header_len, 1, id_len, pdu_type, 1, 0, 0])
    return (header + struct.pack(">HH", total_len, 1200) + system_id + bytes([pseudonode, fragment])
            + struct.pack(">IHB", 2, 0xBEEF, flags) + body + trailer)


LLC = bytes.fromhex("fefe03")
GRE_FRAMING, LLC_FRAMING = "gre1", "macvlan"


def make_ethernet(payload, ethertype=0x00fe):
    return bytes.fromhex("000000000002" "000000000001") + struct.pack(">H", ethertype) + payload


def make_frame(payload, framing):
    """gre1 carries the PDU as is; a macvlan prefixes the LLC header and reports the 802.3 length."""
    if framing == GRE_FRAMING:
        return make_ethernet(payload)
    return make_ethernet(LLC + payload, ethertype=min(len(LLC) + len(payload), 1500))


FRR_TLVS = [
    make_tlv(129, b"\xcc\x8e"),
    make_tlv(1, b"\x03\x49\x00\x01"),
    make_tlv(137, b"watcher"),
    make_tlv(134, bytes([192, 168, 77, 1])),
    make_tlv(242, bytes([192, 168, 77, 1, 0, 0, 0])),
    make_tlv(22, make_is_entry()),
    make_tlv(132, bytes([192, 168, 77, 1])),
    make_tlv(135, make_prefix_entry("10.10.25.0", 24)),
]
FOREIGN_TLV_135 = make_tlv(135, make_prefix_entry("8.8.8.8", 32))
LSP_ENTRY = struct.pack(">H", 1100) + OTHER_ROUTER + b"\x00\x00" + struct.pack(">IH", 5, 0x1234)
SNP_SOURCE = WATCHER + b"\x00"


def make_snp(pdu_type, header_len, tlvs):
    """CSNP and PSNP carry summaries of LSPs, here of another router's."""
    body = SNP_SOURCE + (b"\x00" * 8 + b"\xff" * 8 if pdu_type == CSNP_L2 else b"")
    header = bytes([0x83, header_len, 1, 0, pdu_type, 1, 0, 0])
    return header + struct.pack(">H", header_len + len(tlvs)) + body + tlvs


def make_hello():
    tlvs = make_tlv(1, b"\x03\x49\x00\x01")
    header = bytes([0x83, 20, 1, 0, P2P_HELLO, 1, 0, 0, 3]) + WATCHER + struct.pack(">HH", 30, 20 + len(tlvs)) + b"\x00"
    return header + tlvs


def make_lan_hello():
    tlvs = make_tlv(1, b"\x03\x49\x00\x01")
    header = bytes([0x83, 27, 1, 0, LAN_HELLO_L1, 1, 0, 0, 1]) + WATCHER + struct.pack(">HH", 30, 27 + len(tlvs))
    return header + b"\x40" + DEVICE + b"\x01" + tlvs


def make_icmp():
    return bytes.fromhex("450000540000400040010000" "0a0a1921" "0a0a1920" "0800f7ff00010001")


# The L2 LSP FRR 8.5.4 sent on gre1, as tcpdump on the tunnel recorded it
FRR_CAPTURED_LSP = bytes.fromhex(
    "831b0100140100000058048c000210251111000000000005c4e8038102cc8e010403490001890777617463686572"
    "f205c0a84d01008604c0a84d01160b0000000000020000000a008404c0a84d0187080000000a180a0a19")

# (name, packet, verdict, counter that must grow). Bracketed names a defect class of the previous XDP filter.
CASES = [
    ("own L2 LSP captured from FRR on gre1", FRR_CAPTURED_LSP, PASS, "lsp_passed"),
    ("own L2 LSP as FRR emits it", make_lsp(FRR_TLVS), PASS, "lsp_passed"),
    ("own L1 LSP, IS-type bits only", make_lsp(FRR_TLVS, pdu_type=LSP_L1, flags=0x01), PASS, "lsp_passed"),
    ("own L2 LSP with overload bit", make_lsp(FRR_TLVS, flags=0x07), PASS, "lsp_passed"),
    ("own LSP with no TLVs (header-only, purge) [over-strict header-only rule]", make_lsp(), PASS, "lsp_passed"),
    ("own prefix with a sub-TLV is read by length", make_lsp([make_tlv(135, make_prefix_entry("10.10.25.0", 24, b"\x01\x01\x07"))]), PASS, "lsp_passed"),
    ("foreign TLV in a trailer after the PDU is not parsed [loop bounded by packet end]",
     make_lsp(FRR_TLVS, trailer=FOREIGN_TLV_135), PASS, "lsp_passed"),
    ("zero padding after the PDU", make_lsp(FRR_TLVS, trailer=bytes(16)), PASS, "lsp_passed"),
    ("MT IS reach to one neighbour", make_lsp([make_tlv(222, b"\x00\x02" + make_is_entry())]), PASS, "lsp_passed"),
    ("MT IP reach with the tunnel prefix", make_lsp([make_tlv(235, b"\x00\x02" + make_prefix_entry("10.10.25.0", 24))]), PASS, "lsp_passed"),
    ("narrow IP reach with the tunnel prefix", make_lsp([make_tlv(128, bytes([10, 0x80, 0x80, 0x80, 10, 10, 25, 0, 255, 255, 255, 0]))]), PASS, "lsp_passed"),
    ("point-to-point hello", make_hello(), PASS, None),
    ("LAN hello L1", make_lan_hello(), PASS, None),
    ("CSNP listing another router's LSP", make_snp(CSNP_L2, 33, make_tlv(9, LSP_ENTRY)), PASS, None),
    ("PSNP listing another router's LSP", make_snp(PSNP_L2, 17, make_tlv(9, LSP_ENTRY)), PASS, None),
    ("PSNP with no TLVs, exactly its 17-byte header", make_snp(PSNP_L2, 17, b""), PASS, None),
    ("CSNP with no TLVs, exactly its 33-byte header", make_snp(CSNP_L2, 33, b""), PASS, None),

    ("another router's LSP", make_lsp(FRR_TLVS, system_id=OTHER_ROUTER), DROP, "foreign_lsp"),
    ("system id differs in the last byte only", make_lsp(FRR_TLVS, system_id=bytes.fromhex("000210251112")), DROP, "foreign_lsp"),
    ("own pseudonode LSP", make_lsp(FRR_TLVS, pseudonode=1), DROP, "foreign_lsp"),
    ("own fragment 1: only fragment 0 leaves, so neighbours cannot be split across fragments", make_lsp(FRR_TLVS, fragment=1), DROP, "foreign_lsp"),
    ("second prefix 8.8.8.8/32 in the tunnel prefix TLV", make_lsp([make_tlv(135, make_prefix_entry("10.10.25.0", 24) + make_prefix_entry("8.8.8.8", 32))]), DROP, "prefix"),
    ("8.8.8.8/32 in its own TLV after the usual ones", make_lsp(FRR_TLVS + [FOREIGN_TLV_135]), DROP, "prefix"),
    ("8.8.8.8/32 instead of the tunnel prefix", make_lsp([FOREIGN_TLV_135]), DROP, "prefix"),
    ("8.8.8.8/32 as the last of 24 TLVs", make_lsp([make_tlv(137, b"w")] * 23 + [FOREIGN_TLV_135]), DROP, "prefix"),
    ("tunnel network with another length", make_lsp([make_tlv(135, make_prefix_entry("10.10.25.0", 25))]), DROP, "prefix"),
    ("default route", make_lsp([make_tlv(135, make_prefix_entry("0.0.0.0", 0))]), DROP, "prefix"),
    ("default route next to the tunnel prefix", make_lsp([make_tlv(135, make_prefix_entry("10.10.25.0", 24) + make_prefix_entry("0.0.0.0", 0))]), DROP, "prefix"),
    ("default route in its own TLV after the usual ones", make_lsp(FRR_TLVS + [make_tlv(135, make_prefix_entry("0.0.0.0", 0))]), DROP, "prefix"),
    ("MT IP reach default route", make_lsp([make_tlv(235, b"\x00\x02" + make_prefix_entry("0.0.0.0", 0))]), DROP, "prefix"),
    ("narrow IP reach default route", make_lsp([make_tlv(128, bytes([10, 0x80, 0x80, 0x80, 0, 0, 0, 0, 0, 0, 0, 0]))]), DROP, "prefix"),
    ("IPv6 default route", make_lsp([make_tlv(236, struct.pack(">IBB", 10, 0, 0))]), DROP, "prefix"),
    ("attached bit on an L1 LSP, which L1 routers read as a default route", make_lsp(FRR_TLVS, pdu_type=LSP_L1, flags=0x08), DROP, "attached"),
    ("attached bit on an L2 LSP", make_lsp(FRR_TLVS, flags=0x09), DROP, "attached"),
    ("expense attached bit", make_lsp(FRR_TLVS, pdu_type=LSP_L1, flags=0x20), DROP, "attached"),
    ("foreign prefix with a sub-TLV", make_lsp([make_tlv(135, make_prefix_entry("8.8.8.8", 32, b"\x01\x01\x07"))]), DROP, "prefix"),
    ("narrow IP reach, foreign prefix", make_lsp([make_tlv(128, bytes([10, 0x80, 0x80, 0x80, 8, 8, 8, 8, 255, 255, 255, 255]))]), DROP, "prefix"),
    ("external IP reach", make_lsp([make_tlv(130, bytes([10, 0x80, 0x80, 0x80, 10, 10, 25, 0, 255, 255, 255, 0]))]), DROP, "prefix"),
    ("MT IP reach, foreign prefix", make_lsp([make_tlv(235, b"\x00\x02" + make_prefix_entry("8.8.8.8", 32))]), DROP, "prefix"),
    ("IPv6 reach", make_lsp([make_tlv(236, struct.pack(">IBB", 10, 0, 64) + bytes(8))]), DROP, "prefix"),
    ("MT IPv6 reach", make_lsp([make_tlv(237, b"\x00\x02" + struct.pack(">IBB", 10, 0, 64) + bytes(8))]), DROP, "prefix"),
    ("two neighbours in one IS reach TLV", make_lsp([make_tlv(22, make_is_entry() + make_is_entry(OTHER_ROUTER))]), DROP, "neighbor"),
    ("neighbour behind a pseudonode", make_lsp([make_tlv(22, make_is_entry(pseudonode=1))]), DROP, "neighbor"),
    ("MT IS reach with two neighbours", make_lsp([make_tlv(222, b"\x00\x02" + make_is_entry() + make_is_entry(OTHER_ROUTER))]), DROP, "neighbor"),
    ("two IS reach TLVs with one different neighbour each", make_lsp([make_tlv(22, make_is_entry()), make_tlv(22, make_is_entry(OTHER_ROUTER))]), DROP, "neighbor"),
    ("IS reach and MT IS reach with different neighbours", make_lsp([make_tlv(22, make_is_entry()), make_tlv(222, b"\x00\x02" + make_is_entry(OTHER_ROUTER))]), DROP, "neighbor"),
    ("narrow-metric IS reach", make_lsp([make_tlv(2, b"\x00" + bytes([10, 0x80, 0x80, 0x80]) + DEVICE + b"\x00")]), DROP, "neighbor"),

    ("PSNP one byte short of its header", make_snp(PSNP_L2, 17, b"")[:16], DROP, "truncated"),
    ("10-byte point-to-point hello", make_hello()[:10], DROP, "truncated"),
    ("CSNP declaring more bytes than the packet has", make_snp(CSNP_L2, 33, make_tlv(9, LSP_ENTRY))[:8] + struct.pack(">H", 900) + make_snp(CSNP_L2, 33, make_tlv(9, LSP_ENTRY))[10:], DROP, "truncated"),
    ("PSNP with a header length of 33", make_snp(PSNP_L2, 33, make_tlv(9, LSP_ENTRY)), DROP, "truncated"),
    ("LSP cut short: packet is shorter than the PDU length", make_lsp(FRR_TLVS)[:-10], DROP, "truncated"),
    ("PDU length below the LSP header", make_lsp(FRR_TLVS, pdu_len=20), DROP, "truncated"),
    ("IS-IS packet shorter than the common header [fail-open on parse failure]", bytes([0x83, 27, 1, 0]), DROP, "truncated"),
    ("header length field is not 27", make_lsp(FRR_TLVS, header_len=30), DROP, "truncated"),
    ("PDU larger than the filter reads", make_lsp([make_tlv(137, bytes(250))] * 6, pdu_len=None), DROP, "truncated"),
    ("TLV length runs past the PDU", make_lsp(FRR_TLVS + [bytes([137, 200]) + b"x"]), DROP, "malformed_tlv"),
    ("one stray byte after the last TLV", make_lsp(FRR_TLVS + [b"\x00"]), DROP, "malformed_tlv"),
    ("more TLVs than the filter walks", make_lsp([make_tlv(137, b"w")] * 25), DROP, "malformed_tlv"),
    ("system id length other than 6", make_lsp(FRR_TLVS, id_len=8), DROP, "malformed_tlv"),
    ("unknown PDU type", bytes([0x83, 27, 1, 0, 30, 1, 0, 0]) + bytes(30), DROP, "unknown_pdu"),
]

IPV4_ETHERTYPE, ARP_ETHERTYPE = 0x0800, 0x0806
STP_BPDU = bytes.fromhex("424203" "000000000000")
ES_IS_HELLO = bytes.fromhex("82" "0900") + bytes(10)

# (name, ethertype, bytes after the Ethernet header, verdict, counter). Frames that are not IS-IS framing or carry no IS-IS.
OTHER_CASES = [
    ("IPv4 packet in the tunnel", 0x00fe, make_icmp(), PASS, None),
    ("IPv4 packet with an IPv4 ethertype", IPV4_ETHERTYPE, make_icmp(), PASS, None),
    ("ARP request", ARP_ETHERTYPE, bytes.fromhex("0001080006040001") + bytes(20), PASS, None),
    ("ARP-ethertype frame whose first byte reads as an IS-IS NLPID", ARP_ETHERTYPE, bytes([0x83]) + bytes(40), PASS, None),
    ("ARP-ethertype frame that starts with the LLC header and an IS-IS NLPID", ARP_ETHERTYPE, LLC + bytes([0x83]) + bytes(40), PASS, None),
    ("IPv6 neighbour solicitation", 0x86dd, bytes.fromhex("6000000000203aff") + bytes(32), PASS, None),
    ("STP BPDU in an 802.3 frame", len(STP_BPDU), STP_BPDU, PASS, None),
    ("ES-IS hello behind the LLC header", len(LLC) + len(ES_IS_HELLO), LLC + ES_IS_HELLO, PASS, None),
    ("LLC header with another second byte", 8, bytes.fromhex("fe0103") + bytes(5), PASS, None),
    ("LLC header cut after two bytes", 2, bytes.fromhex("fefe"), DROP, "truncated"),
    ("LLC header and nothing behind it", 3, LLC, DROP, "truncated"),
    ("empty 802.3 frame", 0, b"", DROP, "truncated"),
    ("IS-IS packet behind the LLC header, one byte of PDU", 4, LLC + bytes([0x83]), DROP, "truncated"),
]


def run(command, **kwargs):
    return subprocess.run(command, check=True, capture_output=True, text=True, **kwargs).stdout


def attach_filter():
    run(["ip", "link", "add", INTERFACE, "type", "dummy"])
    run(["ip", "link", "set", INTERFACE, "up"])
    environment = dict(os.environ, WATCHER_SYSTEM_ID=WATCHER_SYSTEM_ID, TUNNEL_NETWORK=TUNNEL_NETWORK,
                       TUNNEL_PREFIX_LEN=TUNNEL_PREFIX_LEN)
    run(["/opt/isis-filter/attach_filter.sh"], env=environment)
    program_id = re.search(r"\bid (\d+)", run(["tc", "filter", "show", "dev", INTERFACE, "egress"])).group(1)
    map_ids = re.search(r"map_ids ([\d,]+)", run(["bpftool", "prog", "show", "id", program_id])).group(1)
    return program_id, [int(map_id) for map_id in map_ids.split(",")]


def read_counters(map_ids):
    """The counters map is the only 8-byte-value array among the program's maps."""
    for map_id in map_ids:
        entries = json.loads(run(["bpftool", "-j", "map", "dump", "id", str(map_id)]))
        if len(entries) != len(COUNTER_NAMES):
            continue
        return {COUNTER_NAMES[index]: int.from_bytes(bytes(int(byte, 16) for byte in entry["value"]), "little")
                for index, entry in enumerate(entries)}
    raise RuntimeError("counters map not found")


def get_verdict(program_id, frame):
    with tempfile.NamedTemporaryFile() as packet_file:
        packet_file.write(frame)
        packet_file.flush()
        output = run(["bpftool", "prog", "run", "id", program_id, "data_in", packet_file.name, "repeat", "1"])
    return {TC_ACT_OK: PASS, TC_ACT_SHOT: DROP}[int(re.search(r"Return value: (\d+)", output).group(1))]


def main():
    program_id, map_ids = attach_filter()
    cases = [(f"{framing}: {name}", make_frame(packet, framing), verdict, counter)
             for name, packet, verdict, counter in CASES for framing in (GRE_FRAMING, LLC_FRAMING)]
    cases += [(f"raw: {name}", make_ethernet(payload, ethertype), verdict, counter)
              for name, ethertype, payload, verdict, counter in OTHER_CASES]
    failed = 0
    for name, frame, expected_verdict, expected_counter in cases:
        before = read_counters(map_ids)
        verdict = get_verdict(program_id, frame)
        after = read_counters(map_ids)
        grown = [counter for counter in COUNTER_NAMES if after[counter] != before[counter]]
        is_ok = verdict == expected_verdict and grown == ([expected_counter] if expected_counter else [])
        failed += not is_ok
        print(f"{'ok   ' if is_ok else 'WRONG'} want {expected_verdict} {expected_counter or '-':14} got {verdict} {','.join(grown) or '-':14} {name}")
    print(f"{len(cases) - failed}/{len(cases)} cases as expected")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
