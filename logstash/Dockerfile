ARG LOGSTASH_OSS_VERSION

# https://www.docker.elastic.co/
FROM docker.elastic.co/logstash/logstash-oss:${LOGSTASH_OSS_VERSION}

# Add your logstash plugins setup here
# Example: RUN logstash-plugin install logstash-filter-json
RUN logstash-plugin install logstash-codec-csv
RUN logstash-plugin install logstash-output-zabbix
RUN logstash-plugin install --version=3.1.5 logstash-output-mongodb
