FROM alpine:3.20
RUN apk add --no-cache clang iproute2-minimal iproute2-tc libbpf-dev linux-headers bpftool python3
COPY isis_filter.c attach.sh /opt/isis-filter/
COPY tests/check_cases.py /opt/isis-filter/
