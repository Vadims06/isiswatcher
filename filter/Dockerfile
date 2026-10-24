FROM alpine:3.20
RUN apk add --no-cache clang iproute2-minimal iproute2-tc libbpf-dev linux-headers
COPY announcement_filter.c attach_filter.sh /opt/isis-filter/
ENTRYPOINT ["/opt/isis-filter/attach_filter.sh"]
