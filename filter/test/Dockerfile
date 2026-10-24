FROM alpine:3.20
RUN apk add --no-cache clang iproute2-minimal iproute2-tc libbpf-dev linux-headers bpftool python3
COPY announcement_filter.c attach_filter.sh /opt/isis-filter/
COPY test/check_cases.py /opt/isis-filter/
