import pathlib
import shutil
import re
import sys
import types

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
pytest.importorskip("jinja2")
pytest.importorskip("ruamel.yaml")


def get_gre_rules(monkeypatch, watcher_num, router_ip):
    """Rules of one GRE watcher as its topology records them."""
    monkeypatch.chdir(REPO_ROOT)
    monkeypatch.syspath_prepend(str(REPO_ROOT))
    # diagnostic needs scapy and netns, which only the watcher image has
    monkeypatch.setitem(sys.modules, "diagnostic", types.ModuleType("diagnostic"))
    import client
    watcher = client.WATCHER_CONFIG(watcher_num)
    watcher.gre_tunnel_network_device_ip = router_ip
    watcher.host_interface_device_ip = "192.168.0.140"
    watcher.gre_tunnel_number = watcher_num
    watcher.gre_tunnel_ip_w_mask_watcher = f"10.99.{watcher_num}.2/30"
    watcher.host_veth = f"isis{watcher_num}-gre{watcher_num}"
    return [match["rule"] for command in watcher.exec_cmds() for match in [client.GRE_RULE_RE.search(command)] if match]


def test_gre_rules_of_two_watchers_never_coincide(monkeypatch):
    # The same router behind two hosts gives identical FORWARD rules, so only the comment tells their owner
    first = get_gre_rules(monkeypatch, 1, "172.31.50.2")
    second = get_gre_rules(monkeypatch, 2, "172.31.50.2")

    assert len(first) == 4
    assert not set(first) & set(second)
    assert all(re.search(r"--comment topolograph-isiswatcher-watcher1-gre1-isis\b", rule) for rule in first)


def build_watcher(monkeypatch, tmp_path, enable_filter):
    """A GRE watcher built in a scratch checkout; returns the client module, the watcher and its folder."""
    shutil.copytree(REPO_ROOT / "watcher" / "watcher-template", tmp_path / "watcher" / "watcher-template")
    monkeypatch.chdir(tmp_path)
    monkeypatch.syspath_prepend(str(REPO_ROOT))
    monkeypatch.setitem(sys.modules, "diagnostic", types.ModuleType("diagnostic"))
    import client
    watcher = client.WATCHER_CONFIG(3)
    watcher.gre_tunnel_network_device_ip = "172.31.50.2"
    watcher.host_interface_device_ip = "192.168.0.140"
    watcher.gre_tunnel_number = 1025
    watcher.gre_tunnel_ip_w_mask_network_device = "10.99.3.1/30"
    watcher.gre_tunnel_ip_w_mask_watcher = "10.99.3.2/30"
    watcher.isis_area_num = "49.0001"
    watcher.is_filter_enabled = enable_filter
    watcher.create_folder_with_settings()
    return client, watcher


def get_watcher_wait_for(nodes):
    return [dict(item) for item in nodes["isis-watcher"]["stages"]["create"]["wait-for"]]


def get_nodes(watcher):
    return watcher.watcher_config_file_yml["topology"]["nodes"]


def test_filter_node_carries_the_watcher_identity_and_replaces_h2(monkeypatch, tmp_path):
    client, watcher = build_watcher(monkeypatch, tmp_path, enable_filter=True)

    nodes = get_nodes(watcher)
    assert dict(nodes["receive_only_filter"]["env"]) == {
        "WATCHER_SYSTEM_ID": "0003.1025.1111", "TUNNEL_NETWORK": "10.99.3.0", "TUNNEL_PREFIX_LEN": "30"}
    assert nodes["receive_only_filter"]["image"] == "vadims06/isis-filter:v1"
    assert nodes["receive_only_filter"]["network-mode"] == "container:router"
    assert "h2" not in nodes
    assert get_watcher_wait_for(nodes) == [{"node": "receive_only_filter", "stage": "exit"}]
    # FRR's own system id is the one the filter lets out
    assert "net 49.0001.0003.1025.1111.00" in (pathlib.Path(watcher.router_folder_path) / "frr.conf").read_text()


def test_without_filter_h2_brings_the_tunnel_up_after_h1(monkeypatch, tmp_path):
    client, watcher = build_watcher(monkeypatch, tmp_path, enable_filter=False)

    nodes = get_nodes(watcher)
    assert "receive_only_filter" not in nodes
    assert [dict(item) for item in nodes["h2"]["stages"]["create"]["wait-for"]] == [{"node": "h1", "stage": "create"}]
    assert get_watcher_wait_for(nodes) == [{"node": "h2", "stage": "create"}]
    assert list(nodes["h2"]["exec"]) == [f"sudo ip netns exec {watcher.netns_name} ip link set up dev gre1"]


def test_filter_actions_switch_an_existing_watcher(monkeypatch, tmp_path):
    client, watcher = build_watcher(monkeypatch, tmp_path, enable_filter=False)

    client.WATCHER_CONFIG(3).run_command("enable_filter")
    assert "receive_only_filter" in get_nodes(watcher) and "h2" not in get_nodes(watcher)
    assert get_watcher_wait_for(get_nodes(watcher)) == [{"node": "receive_only_filter", "stage": "exit"}]

    client.WATCHER_CONFIG(3).run_command("disable_filter")
    assert "receive_only_filter" not in get_nodes(watcher) and "h2" in get_nodes(watcher)
    assert get_watcher_wait_for(get_nodes(watcher)) == [{"node": "h2", "stage": "create"}]


def test_pinned_filter_image_follows_the_registry_prefix(monkeypatch, tmp_path):
    client, watcher = build_watcher(monkeypatch, tmp_path, enable_filter=True)
    (tmp_path / "VERSION").write_text("v9.9.9\n")
    monkeypatch.setenv("REGISTRY_PREFIX", "mirror.example.com:5000/")

    assert client.WATCHER_CONFIG.get_pinned_images()["receive_only_filter"] == "mirror.example.com:5000/vadims06/isis-filter:v1"
