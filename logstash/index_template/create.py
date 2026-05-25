import requests
import json, os, sys
if __name__ == '__main__':
    if os.getenv('EXPORT_TO_ELASTICSEARCH_BOOL', 'False') == 'False':
        # ELK is disable
        sys.exit(0)
    ELASTIC_IP = os.getenv('ELASTIC_IP', '') if os.getenv('ELASTIC_IP') else 'http://172.25.80.1'
    print(f'ELASTIC_IP:{ELASTIC_IP}')
    ELASTIC_URL = 'http://' + ELASTIC_IP
    ELASTIC_USER_LOGIN = os.getenv('ELASTIC_USER_LOGIN', 'elastic')
    ELASTIC_USER_PASS = os.getenv('ELASTIC_USER_PASS', 'changeme')
    headers = {'Content-Type':'application/json'}

    indexTempateNameToSettings = {} 
    indexTempateNameToSettings['isis-watcher-updown-events'] = {'index_patterns': ['isis-watcher-updown-events*'], 'template': {'mappings': {'dynamic': False, 'properties': {"@timestamp": {"type": "date"},"watcher_time": { "type": "date", "format": "date_optional_time"},"watcher_time_iso8601": { "type": "date", "format": "date_optional_time"},"watcher_name": {"type": "keyword"},"level_number": {"type": "keyword"},"event_name": {"type": "keyword"},"event_object": {"type": "keyword"},"event_status": {"type": "keyword"},"old_cost": {"type": "integer"},"new_cost": {"type": "integer"},"event_detected_by": {"type": "keyword"},"graph_time": {"type": "keyword"},"asn": {"type": "keyword"},"area_num": {"type": "keyword"},"local_ip_address": {"type": "ip"},"remote_ip_address": {"type": "ip"}}}}, '_meta': {'description': 'IS-IS index template for Watcher logs'}, 'allow_auto_create': True}
    indexTempateNameToSettings['isis-watcher-costs-changes'] = {'index_patterns': ['isis-watcher-costs-changes*'], 'template': {'mappings': {'dynamic': False, 'properties': {"@timestamp": {"type": "date"},"watcher_time": { "type": "date", "format": "date_optional_time"},"watcher_time_iso8601": { "type": "date", "format": "date_optional_time"},"watcher_name": {"type": "keyword"},"level_number": {"type": "keyword"},"event_name": {"type": "keyword"},"event_object": {"type": "keyword"},"event_status": {"type": "keyword"},"old_cost": {"type": "integer"},"new_cost": {"type": "integer"},"event_detected_by": {"type": "keyword"},"subnet_type": {"type": "keyword"},"graph_time": {"type": "keyword"},"asn": {"type": "keyword"},"area_num": {"type": "keyword"},"int_ext_subtype": {"type": "integer"}}}}, '_meta': {'description': 'IS-IS index template for Watcher costs changes logs'}, 'allow_auto_create': True}
    temetric_properties = {
        "@timestamp": {"type": "date"},
        "watcher_time": { "type": "date", "format": "date_optional_time"},
        "watcher_time_iso8601": { "type": "date", "format": "date_optional_time"},
        "watcher_name": {"type": "keyword"},
        "level_number": {"type": "short"},
        "event_name": {"type": "keyword"},
        "event_object": {"type": "keyword"},
        "event_status": {"type": "keyword"},
        "admin_groups": {"type": "short"},
        "max_link_bw": {"type": "integer"},
        "max_rsrv_link_bw": {"type": "integer"},
        "unreserved_bandwidth_0": {"type": "integer"},
        "unreserved_bandwidth_1": {"type": "integer"},
        "unreserved_bandwidth_2": {"type": "integer"},
        "unreserved_bandwidth_3": {"type": "integer"},
        "unreserved_bandwidth_4": {"type": "integer"},
        "unreserved_bandwidth_5": {"type": "integer"},
        "unreserved_bandwidth_6": {"type": "integer"},
        "unreserved_bandwidth_7": {"type": "integer"},
        "temetric": {"type": "integer"},
        "event_detected_by": {"type": "keyword"},
        "graph_time": {"type": "keyword"},
        "area_num": {"type": "keyword"},
        "asn": {"type": "keyword"},
        "local_ip_address": {"type": "ip"},
        "remote_ip_address": {"type": "ip"}
    }
    indexTempateNameToSettings['isis-watcher-temetric-changes'] = {'index_patterns': ['isis-watcher-temetric-changes*'], 'template': {'mappings': {'dynamic': False, 'properties': temetric_properties}}, '_meta': {'description': 'IS-IS index template for Watcher costs changes logs'}, 'allow_auto_create': True}

    for indexTemplateName, indexTemplateSettings in indexTempateNameToSettings.items():
        r = requests.put(f"{ELASTIC_URL}:9200/_index_template/{indexTemplateName}", auth=(ELASTIC_USER_LOGIN, ELASTIC_USER_PASS), headers=headers, data=json.dumps(indexTemplateSettings))
        print(r.json())
        if not r.ok:
            reply_dd = r.json()
            if isinstance(reply_dd, dict) and "unable to authenticate user" in reply_dd.get('error', {}).get('reason', ''):
                print(f"{'*'*10} Error {'*'*10}")
                print(f"The script was not able to create Index Templates because it couldn't authenticate in ELK. In most cases xpack.security.enabled: true is a reason, because it requires certificate of ELK. ")
                print(f"{'*'*10} Error {'*'*10}")