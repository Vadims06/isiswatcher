/* Listen-only filter for the watcher's FRR: of all IS-IS LSPs only the watcher's own may leave gre1 or a macvlan. */
#include <linux/bpf.h>
#include <linux/pkt_cls.h>
#include <linux/if_ether.h>
#include <bpf/bpf_helpers.h>
#include <bpf/bpf_endian.h>

#if !defined(WATCHER_SYSTEM_ID_BYTES) || !defined(TUNNEL_NETWORK) || !defined(TUNNEL_PREFIX_LEN)
#error "set WATCHER_SYSTEM_ID_BYTES, TUNNEL_NETWORK and TUNNEL_PREFIX_LEN"
#endif

#define NLPID_ISIS 0x83
#define LLC_BYTE 0xfe
#define LLC_CONTROL 0x03
#define LLC_LEN 3
#define LSP_FLAGS 26
#define LSP_ATT_BITS 0x78
#define IIH_LENGTH_FIELD_END 19
#define SNP_LENGTH_FIELD_END 10
#define IIH_LENGTH_FIELD 17
#define SNP_LENGTH_FIELD 8
#define P2P_IIH_HEADER_LEN 20
#define LAN_IIH_HEADER_LEN 27
#define CSNP_HEADER_LEN 33
#define PSNP_HEADER_LEN 17
#define LSP_HEADER_LEN 27
#define PDU_TYPE_MASK 0x1f
#define PDU_LAN_IIH_L1 15
#define PDU_LAN_IIH_L2 16
#define PDU_P2P_IIH 17
#define PDU_LSP_L1 18
#define PDU_LSP_L2 20
#define PDU_CSNP_L1 24
#define PDU_CSNP_L2 25
#define PDU_PSNP_L1 26
#define PDU_PSNP_L2 27

#define TLV_IS_REACH_OLD 2
#define TLV_IP_REACH_INTERNAL 128
#define TLV_IP_REACH_EXTERNAL 130
#define TLV_IS_REACH 22
#define TLV_MT_IS_REACH 222
#define TLV_IP_REACH 135
#define TLV_MT_IP_REACH 235
#define TLV_IPV6_REACH 236
#define TLV_MT_IPV6_REACH 237

/* Larger PDUs or more TLVs than the watcher's own LSP ever carries are dropped, not skipped. */
#define MAX_PDU_LEN 1500
#define SCRATCH_SIZE 2048
#define SCRATCH_MASK (SCRATCH_SIZE - 1)
#define MAX_TLVS 24
#define MAX_PREFIXES_PER_TLV 4

#define TUNNEL_MASK (0xffffffffU << (32 - TUNNEL_PREFIX_LEN))

enum counter {
    COUNTER_LSP_PASSED,
    COUNTER_DROP_TRUNCATED,
    COUNTER_DROP_UNKNOWN_PDU,
    COUNTER_DROP_FOREIGN_LSP,
    COUNTER_DROP_MALFORMED_TLV,
    COUNTER_DROP_NEIGHBOR,
    COUNTER_DROP_PREFIX,
    COUNTER_DROP_ATTACHED,
    COUNTER_MAX,
};

/* Layout read by tc; libbpf does not load this map format */
struct bpf_elf_map {
    __u32 type, size_key, size_value, max_elem, flags, id, pinning, inner_id, inner_idx;
};

struct bpf_elf_map SEC("maps") counters = {
    .type = BPF_MAP_TYPE_ARRAY, .size_key = sizeof(__u32), .size_value = sizeof(__u64), .max_elem = COUNTER_MAX,
};

/* Random access to the PDU without tracking packet pointers; softirq keeps one CPU on one packet */
struct bpf_elf_map SEC("maps") scratch = {
    .type = BPF_MAP_TYPE_PERCPU_ARRAY, .size_key = sizeof(__u32), .size_value = SCRATCH_SIZE, .max_elem = 1,
};

#define AT(offset) pdu[(offset) & SCRATCH_MASK]

static __always_inline int count(enum counter counter, int verdict)
{
    __u32 key = counter;
    __u64 *total = bpf_map_lookup_elem(&counters, &key);

    if (total)
        __sync_fetch_and_add(total, 1);
    return verdict;
}

static __always_inline __u32 get_be32(const __u8 *pdu, __u32 offset)
{
    return ((__u32)AT(offset) << 24) | ((__u32)AT(offset + 1) << 16) | ((__u32)AT(offset + 2) << 8) | AT(offset + 3);
}

/* One adjacency per tunnel, so one neighbour entry, and a point-to-point one (pseudonode 0) */
static __always_inline int get_is_reach_drop(const __u8 *pdu, __u32 start, __u32 length)
{
    if (length == 0)
        return 0;
    if (length < 11)
        return COUNTER_DROP_MALFORMED_TLV;
    if (11 + AT(start + 10) != length || AT(start + 6) != 0)
        return COUNTER_DROP_NEIGHBOR;
    return 0;
}

/* Wide-metric entries: metric(4), flags and length(1), packed prefix, optional sub-TLVs */
static __always_inline int get_ip_reach_drop(const __u8 *pdu, __u32 start, __u32 end)
{
    __u32 position = start;

#pragma unroll
    for (int entry = 0; entry < MAX_PREFIXES_PER_TLV; entry++) {
        if (position == end)
            return 0;
        if (position + 5 > end)
            return COUNTER_DROP_MALFORMED_TLV;
        __u8 control = AT(position + 4);
        __u32 prefix_len = control & 0x3f;
        __u32 prefix_bytes = (prefix_len + 7) >> 3;
        __u32 entry_end = position + 5 + prefix_bytes;
        if (prefix_len > 32)
            return COUNTER_DROP_MALFORMED_TLV;
        if (control & 0x40) {
            if (entry_end + 1 > end)
                return COUNTER_DROP_MALFORMED_TLV;
            entry_end += 1 + AT(entry_end);
        }
        if (entry_end > end)
            return COUNTER_DROP_MALFORMED_TLV;
        __u32 prefix = 0;
#pragma unroll
        for (int byte = 0; byte < 4; byte++)
            prefix = (prefix << 8) | (byte < prefix_bytes ? AT(position + 5 + byte) : 0);
        if (prefix_len != TUNNEL_PREFIX_LEN || prefix != TUNNEL_NETWORK)
            return COUNTER_DROP_PREFIX;
        position = entry_end;
    }
    return position == end ? 0 : COUNTER_DROP_PREFIX;
}

/* Narrow-metric entries: four metrics, address, mask */
static __always_inline int get_old_ip_reach_drop(const __u8 *pdu, __u32 start, __u32 end)
{
    if ((end - start) % 12)
        return COUNTER_DROP_MALFORMED_TLV;
#pragma unroll
    for (int entry = 0; entry < MAX_PREFIXES_PER_TLV; entry++) {
        __u32 position = start + entry * 12;
        if (position == end)
            return 0;
        if (get_be32(pdu, position + 4) != TUNNEL_NETWORK || get_be32(pdu, position + 8) != TUNNEL_MASK)
            return COUNTER_DROP_PREFIX;
    }
    return start + MAX_PREFIXES_PER_TLV * 12 == end ? 0 : COUNTER_DROP_PREFIX;
}

/* A global function is verified once, which keeps the verifier from retracing it for every TLV */
__attribute__((noinline)) int get_tlv_drop(__u32 type, __u32 start, __u32 length)
{
    __u32 key = 0;
    const __u8 *pdu = bpf_map_lookup_elem(&scratch, &key);
    __u32 end = start + length;

    if (!pdu)
        return COUNTER_DROP_MALFORMED_TLV;

    switch (type) {
    case TLV_IS_REACH:
        return get_is_reach_drop(pdu, start, length);
    case TLV_MT_IS_REACH:
        return length < 2 ? COUNTER_DROP_MALFORMED_TLV : get_is_reach_drop(pdu, start + 2, length - 2);
    case TLV_IP_REACH:
        return get_ip_reach_drop(pdu, start, end);
    case TLV_MT_IP_REACH:
        return length < 2 ? COUNTER_DROP_MALFORMED_TLV : get_ip_reach_drop(pdu, start + 2, end);
    case TLV_IP_REACH_INTERNAL:
        return get_old_ip_reach_drop(pdu, start, end);
    case TLV_IS_REACH_OLD:
        return length ? COUNTER_DROP_NEIGHBOR : 0;
    case TLV_IP_REACH_EXTERNAL:
    case TLV_IPV6_REACH:
    case TLV_MT_IPV6_REACH:
        return length ? COUNTER_DROP_PREFIX : 0;
    }
    return 0;
}

static __always_inline long load_pdu(struct __sk_buff *skb, __u32 base, void *to, __u32 length)
{
    return bpf_skb_load_bytes_relative(skb, base, to, length, BPF_HDR_START_NET);
}

SEC("classifier")
int isis_filter(struct __sk_buff *skb)
{
    const __u8 watcher_system_id[6] = {WATCHER_SYSTEM_ID_BYTES};
    __u32 key = 0;
    __u32 base = 0;
    __u8 llc[LLC_LEN];
    __u8 nlpid;

    /* A real ethertype (IP, ARP, IPv6) carries no IS-IS; gre1 and 802.3 frames have protocol below it */
    if (bpf_ntohs(skb->protocol) >= ETH_P_802_3_MIN)
        return TC_ACT_OK;
    /* A packet that cannot be read is not let out */
    if (load_pdu(skb, 0, llc, 1) < 0)
        return count(COUNTER_DROP_TRUNCATED, TC_ACT_SHOT);
    /* gre1 egress starts at the PDU; a macvlan egress starts at the LLC header in front of it */
    if (llc[0] == LLC_BYTE) {
        if (load_pdu(skb, 0, llc, LLC_LEN) < 0)
            return count(COUNTER_DROP_TRUNCATED, TC_ACT_SHOT);
        if (llc[1] != LLC_BYTE || llc[2] != LLC_CONTROL)
            return TC_ACT_OK;
        base = LLC_LEN;
    }
    if (load_pdu(skb, base, &nlpid, 1) < 0)
        return count(COUNTER_DROP_TRUNCATED, TC_ACT_SHOT);
    if (nlpid != NLPID_ISIS)
        return TC_ACT_OK;
    bpf_skb_pull_data(skb, skb->len);

    __u8 *pdu = bpf_map_lookup_elem(&scratch, &key);

    if (!pdu || load_pdu(skb, base, pdu, SNP_LENGTH_FIELD_END) < 0)
        return count(COUNTER_DROP_TRUNCATED, TC_ACT_SHOT);

    __u32 header_len, length_field;
    int is_lsp = 0;

    switch (pdu[4] & PDU_TYPE_MASK) {
    case PDU_P2P_IIH:
    case PDU_LAN_IIH_L1:
    case PDU_LAN_IIH_L2:
        /* The hello length field sits further in; the shortest valid hello covers it */
        header_len = (pdu[4] & PDU_TYPE_MASK) == PDU_P2P_IIH ? P2P_IIH_HEADER_LEN : LAN_IIH_HEADER_LEN;
        length_field = IIH_LENGTH_FIELD;
        if (load_pdu(skb, base, pdu, IIH_LENGTH_FIELD_END) < 0)
            return count(COUNTER_DROP_TRUNCATED, TC_ACT_SHOT);
        break;
    case PDU_CSNP_L1:
    case PDU_CSNP_L2:
        header_len = CSNP_HEADER_LEN;
        length_field = SNP_LENGTH_FIELD;
        break;
    case PDU_PSNP_L1:
    case PDU_PSNP_L2:
        header_len = PSNP_HEADER_LEN;
        length_field = SNP_LENGTH_FIELD;
        break;
    case PDU_LSP_L1:
    case PDU_LSP_L2:
        header_len = LSP_HEADER_LEN;
        length_field = SNP_LENGTH_FIELD;
        is_lsp = 1;
        break;
    default:
        return count(COUNTER_DROP_UNKNOWN_PDU, TC_ACT_SHOT);
    }

    __u32 pdu_len = ((__u32)AT(length_field) << 8) | AT(length_field + 1);

    if (pdu[1] != header_len || pdu_len < header_len || pdu_len > MAX_PDU_LEN)
        return count(COUNTER_DROP_TRUNCATED, TC_ACT_SHOT);
    /* Keeps both bounds in the register the helper reads, which the verifier needs */
    asm volatile("" : "+r"(pdu_len));
    /* Only the PDU is read, so bytes after its length are never parsed */
    if (load_pdu(skb, base, pdu, pdu_len) < 0)
        return count(COUNTER_DROP_TRUNCATED, TC_ACT_SHOT);
    if (!is_lsp)
        return TC_ACT_OK;
    if (pdu[3] != 0 && pdu[3] != 6)
        return count(COUNTER_DROP_MALFORMED_TLV, TC_ACT_SHOT);
    /* ATT makes L1 routers send their default route to the watcher */
    if (pdu[LSP_FLAGS] & LSP_ATT_BITS)
        return count(COUNTER_DROP_ATTACHED, TC_ACT_SHOT);
#pragma unroll
    for (int byte = 0; byte < 6; byte++)
        if (pdu[12 + byte] != watcher_system_id[byte])
            return count(COUNTER_DROP_FOREIGN_LSP, TC_ACT_SHOT);
    /* The own LSP is tiny, so only fragment 0 leaves: a limit of one neighbour could not hold across fragments */
    if (pdu[18] != 0 || pdu[19] != 0)
        return count(COUNTER_DROP_FOREIGN_LSP, TC_ACT_SHOT);

    __u32 offset = LSP_HEADER_LEN;
    int has_neighbor = 0;

    for (int tlv = 0; tlv < MAX_TLVS; tlv++) {
        if (offset == pdu_len)
            return count(COUNTER_LSP_PASSED, TC_ACT_OK);
        if (offset + 2 > pdu_len)
            return count(COUNTER_DROP_MALFORMED_TLV, TC_ACT_SHOT);
        __u32 length = AT(offset + 1);
        if (offset + 2 + length > pdu_len)
            return count(COUNTER_DROP_MALFORMED_TLV, TC_ACT_SHOT);
        __u32 type = AT(offset);
        int reason = get_tlv_drop(type, offset + 2, length);
        /* A limit of one neighbour holds over all IS reach TLVs of the LSP, not per TLV */
        if (!reason && (type == TLV_IS_REACH || type == TLV_MT_IS_REACH) && length > (type == TLV_MT_IS_REACH ? 2 : 0)) {
            reason = has_neighbor ? COUNTER_DROP_NEIGHBOR : 0;
            has_neighbor = 1;
        }
        if (reason)
            return count(reason, TC_ACT_SHOT);
        offset += 2 + length;
    }
    if (offset == pdu_len)
        return count(COUNTER_LSP_PASSED, TC_ACT_OK);
    return count(COUNTER_DROP_MALFORMED_TLV, TC_ACT_SHOT);
}

char _license[] SEC("license") = "GPL";
