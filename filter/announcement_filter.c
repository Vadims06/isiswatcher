/* Listen-only filter for the watcher's FRR: of all IS-IS LSPs only the watcher's own may leave gre1. */
#include <linux/bpf.h>
#include <linux/pkt_cls.h>
#include <bpf/bpf_helpers.h>

#if !defined(WATCHER_SYSTEM_ID_BYTES) || !defined(TUNNEL_NETWORK) || !defined(TUNNEL_PREFIX_LEN)
#error "set WATCHER_SYSTEM_ID_BYTES, TUNNEL_NETWORK and TUNNEL_PREFIX_LEN"
#endif

#define NLPID_ISIS 0x83
#define LSP_LENGTH_FIELD_END 10
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

/* Reads relative to the network header: gre1 has no link header in production, BPF_PROG_TEST_RUN adds one */
static __always_inline long load_pdu(struct __sk_buff *skb, void *to, __u32 length)
{
    return bpf_skb_load_bytes_relative(skb, 0, to, length, BPF_HDR_START_NET);
}

SEC("classifier")
int isis_filter(struct __sk_buff *skb)
{
    const __u8 watcher_system_id[6] = {WATCHER_SYSTEM_ID_BYTES};
    __u32 key = 0;
    __u8 nlpid;

    /* A packet that cannot be read is not let out */
    if (load_pdu(skb, &nlpid, 1) < 0)
        return count(COUNTER_DROP_TRUNCATED, TC_ACT_SHOT);
    if (nlpid != NLPID_ISIS)
        return TC_ACT_OK;
    bpf_skb_pull_data(skb, skb->len);

    __u8 *pdu = bpf_map_lookup_elem(&scratch, &key);

    if (!pdu || load_pdu(skb, pdu, LSP_LENGTH_FIELD_END) < 0)
        return count(COUNTER_DROP_TRUNCATED, TC_ACT_SHOT);

    switch (pdu[4] & PDU_TYPE_MASK) {
    case PDU_LAN_IIH_L1:
    case PDU_LAN_IIH_L2:
    case PDU_P2P_IIH:
    case PDU_CSNP_L1:
    case PDU_CSNP_L2:
    case PDU_PSNP_L1:
    case PDU_PSNP_L2:
        return TC_ACT_OK;
    case PDU_LSP_L1:
    case PDU_LSP_L2:
        break;
    default:
        return count(COUNTER_DROP_UNKNOWN_PDU, TC_ACT_SHOT);
    }

    __u32 pdu_len = ((__u32)pdu[8] << 8) | pdu[9];

    if (pdu[1] != LSP_HEADER_LEN || pdu_len < LSP_HEADER_LEN || pdu_len > MAX_PDU_LEN)
        return count(COUNTER_DROP_TRUNCATED, TC_ACT_SHOT);
    /* Keeps both bounds in the register the helper reads, which the verifier needs */
    asm volatile("" : "+r"(pdu_len));
    /* Only the PDU is read, so bytes after its length are never parsed */
    if (load_pdu(skb, pdu, pdu_len) < 0)
        return count(COUNTER_DROP_TRUNCATED, TC_ACT_SHOT);
    if (pdu[3] != 0 && pdu[3] != 6)
        return count(COUNTER_DROP_MALFORMED_TLV, TC_ACT_SHOT);
#pragma unroll
    for (int byte = 0; byte < 6; byte++)
        if (pdu[12 + byte] != watcher_system_id[byte])
            return count(COUNTER_DROP_FOREIGN_LSP, TC_ACT_SHOT);
    if (pdu[18] != 0)
        return count(COUNTER_DROP_FOREIGN_LSP, TC_ACT_SHOT);

    __u32 offset = LSP_HEADER_LEN;

    for (int tlv = 0; tlv < MAX_TLVS; tlv++) {
        if (offset == pdu_len)
            return count(COUNTER_LSP_PASSED, TC_ACT_OK);
        if (offset + 2 > pdu_len)
            return count(COUNTER_DROP_MALFORMED_TLV, TC_ACT_SHOT);
        __u32 length = AT(offset + 1);
        if (offset + 2 + length > pdu_len)
            return count(COUNTER_DROP_MALFORMED_TLV, TC_ACT_SHOT);
        int reason = get_tlv_drop(AT(offset), offset + 2, length);
        if (reason)
            return count(reason, TC_ACT_SHOT);
        offset += 2 + length;
    }
    if (offset == pdu_len)
        return count(COUNTER_LSP_PASSED, TC_ACT_OK);
    return count(COUNTER_DROP_MALFORMED_TLV, TC_ACT_SHOT);
}

char _license[] SEC("license") = "GPL";
