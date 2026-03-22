function parse_events(tag, timestamp, record)
    local message = record["log"] or record["message"] or ""

    if not string.find(message, "changed") then
        return -1, 0, 0
    end

    local parts = {}
    for part in string.gmatch(message .. ",", "(.-),") do
        table.insert(parts, part)
    end

    if #parts < 14 then
        return 0, timestamp, record
    end

    record["watcher_time"] = parts[1]
    record["watcher_name"] = parts[2]
    record["level_number"] = parts[3]
    record["event_name"] = parts[4]
    record["event_object"] = parts[5]
    record["event_status"] = parts[6]

    if string.find(message, "temetric") then
        if #parts >= 17 then
            record["admin_groups_str"] = parts[7]
            record["max_link_bw"] = parts[8]
            record["max_rsrv_link_bw"] = parts[9]
            record["temetric"] = parts[11]
            record["event_detected_by"] = parts[12]
            record["graph_time"] = parts[13]
            record["area_num"] = parts[14]
            record["asn"] = parts[15]
            record["local_ip_address"] = parts[16]
            record["remote_ip_address"] = parts[17]
            if #parts >= 19 then
                record["sesid"] = parts[18]
                record["srcid"] = parts[19]
            end

            local admin_groups = {}
            for group in string.gmatch(parts[7], "([^_]+)") do
                table.insert(admin_groups, group)
            end
            record["admin_groups"] = admin_groups

            local unreserved_str = parts[10]
            local unreserved_parts = {}
            for p in string.gmatch(unreserved_str, "([^_]+)") do
                table.insert(unreserved_parts, p)
            end
            for i = 0, 7 do
                if unreserved_parts[i + 1] then
                    record["unreserved_bandwidth_" .. i] = unreserved_parts[i + 1]
                end
            end

            record["metadata"] = record["metadata"] or {}
            record["metadata"]["elasticsearch_index"] = "isis-watcher-temetric-changes"
            record["metadata"]["webhook_item_value"] = "IS-IS L" .. record["level_number"] .. " te link attributes changed"
        end
    elseif string.find(message, "metric") then
        if #parts >= 14 then
            local old_cost_part = parts[7]
            local new_cost_part = parts[8]
            record["old_cost"] = string.match(old_cost_part, "old_cost:(.+)") or old_cost_part
            record["new_cost"] = string.match(new_cost_part, "new_cost:(.+)") or new_cost_part
            record["event_detected_by"] = parts[9]
            record["graph_time"] = parts[10]
            record["area_num"] = parts[11]
            record["asn"] = parts[12]
            record["local_ip_address"] = parts[13]
            record["remote_ip_address"] = parts[14]
            if #parts >= 16 then
                record["sesid"] = parts[15]
                record["srcid"] = parts[16]
            end

            record["metadata"] = record["metadata"] or {}
            if record["new_cost"] == "-1" then
                record["object_status"] = "down"
                record["metadata"]["elasticsearch_index"] = "isis-watcher-updown-events"
                record["metadata"]["webhook_item_value"] = "IS-IS L" .. record["level_number"] .. " down between " .. record["event_object"] .. "-" .. record["event_detected_by"]
            elseif record["old_cost"] == "-1" then
                record["object_status"] = "up"
                record["metadata"]["elasticsearch_index"] = "isis-watcher-updown-events"
                record["metadata"]["webhook_item_value"] = "IS-IS L" .. record["level_number"] .. " up between " .. record["event_object"] .. "-" .. record["event_detected_by"]
            else
                record["object_status"] = "changed"
                record["metadata"]["elasticsearch_index"] = "isis-watcher-costs-changes"
                record["metadata"]["webhook_item_value"] = "IS-IS L" .. record["level_number"] .. " link cost changed"
            end
        end
    elseif string.find(message, "network") then
        if #parts >= 14 then
            local old_cost_part = parts[7]
            local new_cost_part = parts[8]
            record["old_cost"] = string.match(old_cost_part, "old_cost:(.+)") or old_cost_part
            record["new_cost"] = string.match(new_cost_part, "new_cost:(.+)") or new_cost_part
            record["event_detected_by"] = parts[9]
            record["graph_time"] = parts[10]
            record["area_num"] = parts[11]
            record["asn"] = parts[12]
            record["subnet_type"] = parts[13]
            record["int_ext_subtype"] = parts[14]
            if #parts >= 16 then
                record["sesid"] = parts[15]
                record["srcid"] = parts[16]
            end

            record["metadata"] = record["metadata"] or {}
            if record["new_cost"] == "-1" then
                record["object_status"] = "down"
                record["metadata"]["elasticsearch_index"] = "isis-watcher-updown-events"
                record["metadata"]["webhook_item_value"] = "IS-IS L" .. record["level_number"] .. " network down"
            elseif record["old_cost"] == "-1" then
                record["object_status"] = "up"
                record["metadata"]["elasticsearch_index"] = "isis-watcher-updown-events"
                record["metadata"]["webhook_item_value"] = "IS-IS L" .. record["level_number"] .. " network up"
            else
                record["object_status"] = "changed"
                record["metadata"]["elasticsearch_index"] = "isis-watcher-costs-changes"
                record["metadata"]["webhook_item_value"] = "IS-IS L" .. record["level_number"] .. " network cost changed"
            end
        end
    end

    return 1, timestamp, record
end
