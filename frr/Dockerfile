ARG FRR_VERSION

FROM quay.io/frrouting/frr:${FRR_VERSION}

COPY ./config/isisd.conf /etc/frr/isisd.conf
#COPY ./config/daemons /etc/frr/daemons

RUN sed -i 's/isisd=no/isisd=yes/g' /etc/frr/daemons && \
    chown frr:frr /etc/frr/isisd.conf && \
    chown frr:frr /etc/frr/isisd.conf && \
    mkdir -p /var/log/frr && \
    chown frr:frr /var/log/frr && \
    touch /var/log/frr/isisd.log && \
    chown frr:frr /var/log/frr/isisd.log