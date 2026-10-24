FROM alpine:3.20
RUN apk add --no-cache clang iproute2-minimal iproute2-tc libbpf-dev linux-headers
COPY isis_filter.c attach.sh /opt/isis-filter/
ENTRYPOINT ["/opt/isis-filter/attach.sh"]
