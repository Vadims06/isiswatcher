/* Listen-only filter for the watcher's FRR: of all IS-IS LSPs only the watcher's own may leave gre1. */
#include <linux/bpf.h>
#include <linux/pkt_cls.h>
#include <bpf/bpf_helpers.h>

#if !defined(WATCHER_SYSTEM_ID_BYTES) || !defined(TUNNEL_NETWORK) || !defined(TUNNEL_PREFIX_LEN)
#error "set WATCHER_SYSTEM_ID_BYTES, TUNNEL_NETWORK and TUNNEL_PREFIX_LEN"
#endif

#define NLPID_ISIS 0x83
#define COMMON_HEADER_LEN 8
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

SEC("classifier")
int isis_filter(struct __sk_buff *skb)
{
    return count(COUNTER_LSP_PASSED, TC_ACT_OK);
}

char _license[] SEC("license") = "GPL";
