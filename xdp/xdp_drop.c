#include <linux/bpf.h> // define XDP program return code: XDP_PASS,..
#include <linux/ip.h> // iphdr
#include <linux/if_ether.h> // ethhdr, ETH_P_IP
#include <bpf/bpf_helpers.h> // SEC() macro.
#include <bpf/bpf_endian.h> // bpf_htons

// enable debug print
//#define DEBUG
// enable packet header dump
//#define DEBUG_PRINT_HEADER_SIZE 64

#define OSI_PROTO_TYPE 0x00fe
#define IPPROTO_GRE		47 /* Cisco GRE tunnels (rfc 1701,1702)	*/
#define L1_LINK_STATE        18
#define L2_LINK_STATE        20

struct gre_header {
    __be16 flags_and_version;
    __be16 proto;
};

#define MAX_LSP_ENTRIES 50 // Curently, there are 43 LSPs

/*
+-------+-------+-------+-------+-------+-------+-------+-------+
|         Intradomain Routeing Protocol Discriminator           |
+-------+-------+-------+-------+-------+-------+-------+-------+
|                       Length Indicator                        |
+-------+-------+-------+-------+-------+-------+-------+-------+
|                  Version/Protocol ID extension                |
+-------+-------+-------+-------+-------+-------+-------+-------+
|                         Reserved = 0                          |
+-------+-------+-------+-------+-------+-------+-------+-------+
|   0   |   0   |   0   |              PDU Type                 |
+-------+-------+-------+-------+-------+-------+-------+-------+
|                         Holding Time                          | 2
+-------+-------+-------+-------+-------+-------+-------+-------+
|                          Checksum                             | 2
+-------+-------+-------+-------+-------+-------+-------+-------+
*/
#pragma pack(1)
struct isis_common_pdu {
    __u8 nlpid;
    __u8 hdrlen;
    __u8 version;
    __u8 idlen;
    __u8 pdutype:5,
        reserv:3;
    __u8 pduversion;
    __u8 hdrreserved;
    __u8 maxareaaddresses;
};

/*
+-------------------------+
|  PDU Length             |     2
+-------------------------+
|  Remaining Lifetime     |     2
+-------------------------+
|   FS LSP ID             |     ID Length + 2
+-------------------------+
| Sequence Number         |     4
+-------------------------+
| Checksum                |     2
+-------------------------+
|Reserved|LSPDBOL|IS Type |     1
+-------------------------+
: Variable-Length Fields  :     Variable
+-------------------------+
*/
#pragma pack(1)
struct lsp {
    __be16 length;
    __be16 lifetime;
    __be32 lspid[2];
    __be32 seqnum;
    __be16 checksum;
    __u8 istype:2,
        overload:1,
        att:4,
        partition:1;
};

#define TLV_TYPE_SIZE 1
#define TLV_LENGHT_SIZE 1

#pragma pack(1)
struct type_value {
    __u8 type;
    __u8 lenght;
    //void *value;
};

#define TLV128_INTERNAL_IP_REACH_TYPE_INT 128
#pragma pack(1)
struct tlv128 {
    struct type_value type_value;
    __be32 metric;
    __be32 ipaddress;
    __be32 network_mask;
};
#define TLV130_EXTERNAL_IP_REACH_TYPE_INT 130

#define TLV135_EXTENDED_IP_REACH_TYPE_INT 135
#pragma pack(1)
struct tlv135 {
    // prefix field is not analysed and only "prefixlength" is used
    struct type_value type_value;
    __be32 metric;
    __u8 prefixlength:6,
        subtlv_bool:1,
        distribution:1;
    //void *prefix;
};

#define TLV236_IPV6_REACH_TYPE_INT 236
#pragma pack(1)
struct tlv236 {
    // prefix field is not analysed and only "prefixlength" is used
    struct type_value type_value;
    __be32 metric;
    __u8 reserv:5,
        updown:1,
        external:1,
        subtlv_bool:1;
    __u8 prefixlength;
    //void *prefix;
};

static __always_inline int calc_cidr_lenght(int prefixlength) {
/*
   The prefix is "packed" in the data structure.  That is, only the
   required number of octets of prefix are present.  This number can be
   computed from the prefix length octet as follows:
   prefix octets = integer of ((prefix length + 7) / 8)
*/
    return ((prefixlength + 7) / 8);
}

SEC("xdp_drop")
int xdp_isis_tlv_func(struct xdp_md *ctx) {
	// for border checking
	void *data = (void *)(long)ctx->data;
	void *data_end = (void *)(long)ctx->data_end;
    struct ethhdr *eth = data;

    if (data + sizeof(struct ethhdr) > data_end) goto pass;
    // IP Header
    data += sizeof(struct ethhdr);
    #ifdef DEBUG
		bpf_printk("New packet\n");
	#endif

	// debug print packet header
    
	#if (defined DEBUG_PRINT_HEADER_SIZE) && (DEBUG_PRINT_HEADER_SIZE > 0)
		// check for out of boarder access is necessary, kernel will run static analysis on our program
		if ((data + DEBUG_PRINT_HEADER_SIZE) > data_end) {
			bpf_printk("Packet size too small, dump failed\n");
			return XDP_PASS;
		}
		__u8 *data_raw = (__u8 *)data;
		bpf_printk("Packet header dump:\n");
		#pragma unroll
		for (int i = 0; i < DEBUG_PRINT_HEADER_SIZE; ++i) {
			bpf_printk("#%d: %x\n", i, data_raw[i]);
		}
	#endif

    if ( eth->h_proto == bpf_htons(ETH_P_IP)) {

        if (data + sizeof(struct iphdr) > data_end) goto pass;

        struct iphdr *ip = (struct iphdr *)(data);
        if ((ip->protocol) != IPPROTO_GRE) goto pass;
        // GRE Header
        data += sizeof(struct iphdr);

        if (data + sizeof(struct gre_header) > data_end) goto pass;
        struct gre_header *gre = (struct gre_header *)(data);
        #ifdef DEBUG
            bpf_printk("GRE proto: %x, flags_and_version: %x\n", gre->proto, gre->flags_and_version);
        #endif
        if ((gre->proto) != bpf_htons(OSI_PROTO_TYPE)) goto pass;
        // PDU Header
        data += sizeof(struct gre_header);
        if (data + sizeof(struct isis_common_pdu) > data_end) goto pass;

        struct isis_common_pdu *pdu = (struct isis_common_pdu *)(data);
        data += sizeof(struct isis_common_pdu);
        #ifdef DEBUG
            bpf_printk("ISIS nlpid:  %x\n", pdu->nlpid);
            bpf_printk("ISIS hdrlen:  %x\n", pdu->hdrlen);
            bpf_printk("PDU type: %x\n", pdu->pdutype);
        #endif

        if ((pdu->pdutype != 0x12) && (pdu->pdutype != 0x14)) goto pass;

        if (data + sizeof(struct lsp) > data_end) goto pass;
        // LSP Header
        struct lsp *lsp = (struct lsp *)(data);
        #ifdef DEBUG
            bpf_printk("LSP lenght: %x, %i\n", bpf_htons(lsp->length), (int)bpf_htons(lsp->length));
            bpf_printk("LSP lifetime: %x, %i\n", bpf_htons(lsp->lifetime), (int)bpf_htons(lsp->lifetime));
        #endif
        // TLV
        data += sizeof(struct lsp);
        
        if (data + sizeof(struct type_value) > data_end) goto pass;

        // checking TLV one by one
        #pragma unroll
        for (int tlv_i=0; tlv_i < MAX_LSP_ENTRIES; tlv_i++) {
            if (data + sizeof(struct type_value) > data_end) goto pass;
            struct type_value *type_value = (struct type_value *)(data);
            int size_of_tlv_header = TLV_TYPE_SIZE + TLV_LENGHT_SIZE;
            int size_of_tlv = TLV_TYPE_SIZE + TLV_LENGHT_SIZE + (int)type_value->lenght;
            #ifdef DEBUG
                bpf_printk("TLV type: %x, %i\n", type_value->type, (int)type_value->type);
                bpf_printk("TLV Lenght:  %i\n",  (int)type_value->lenght);
                bpf_printk("Full TLV size: %i\n", size_of_tlv);
            #endif

            if (type_value->type == TLV130_EXTERNAL_IP_REACH_TYPE_INT) {
                bpf_printk("TLV130 External IP reachability detected. Drop!\n");
                return XDP_DROP;
            } else if (type_value->type == TLV128_INTERNAL_IP_REACH_TYPE_INT) {
                #ifdef DEBUG
                    bpf_printk("TLV128, length: %i\n", (int)type_value->lenght);
                #endif
                if ((int)type_value->lenght > 12) {
                    bpf_printk("TLV128 has more than 1 network. Drop!\n");
                    return XDP_DROP;
                }
            } else if (type_value->type == TLV135_EXTENDED_IP_REACH_TYPE_INT) {
                #ifdef DEBUG
                    bpf_printk("TLV135, length: %i\n", (int)type_value->lenght);
                #endif
                if (data + sizeof(struct tlv135) > data_end) {
                    goto pass;
                }

                struct tlv135 *tlv135 = (struct tlv135 *)(data);
                int calculated_prefix_lenght = calc_cidr_lenght((int)tlv135->prefixlength);
                int full_lenght_first_tlv = sizeof(tlv135->metric) + 1 + calculated_prefix_lenght; // 1 bytes embrasses distribution, subtlv, prefix lenght fields
                // TLV includes a single network if length of TLV is equal to Length of first TLV
                if ((int)type_value->lenght > full_lenght_first_tlv) {
                    bpf_printk("TLV135 has more than 1 network - drop!\n");
                    return XDP_DROP;
                }
            } else if (type_value->type == TLV236_IPV6_REACH_TYPE_INT) {
                #ifdef DEBUG
                    bpf_printk("TLV236, length: %i\n", (int)type_value->lenght);
                #endif
                if (data + sizeof(struct tlv236) > data_end) goto pass;
 
                struct tlv236 *tlv236 = (struct tlv236 *)(data);
                #ifdef DEBUG
                    bpf_printk("TLV236, prefixlength: %x, %i\n", tlv236->prefixlength, (int)tlv236->prefixlength);
                    bpf_printk("TLV236, metric: %x, %i\n", bpf_htons(tlv236->metric), (int)bpf_htons(tlv236->metric));
                #endif

                int calculated_prefix_lenght = calc_cidr_lenght((int)tlv236->prefixlength);
                int full_lenght_first_tlv = sizeof(tlv236->metric) + 1 + sizeof(tlv236->prefixlength) + calculated_prefix_lenght; // 1 bytes embrasses updown, external, subtlv fields

                if ((int)type_value->lenght > full_lenght_first_tlv) {
                    bpf_printk("TLV236 has more than 1 network - drop!\n");
                    return XDP_DROP;
                }
            }
            #ifdef DEBUG
                bpf_printk("------------\n");
            #endif
            // shift pointer to the next TLV
            if (data + size_of_tlv > data_end) goto pass;
            data += size_of_tlv;
        }
    }
    bpf_printk("\n");
    return XDP_PASS;
pass:
	return XDP_PASS;
}

char _license[4] SEC("license") = "GPL";