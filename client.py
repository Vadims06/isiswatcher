import argparse
import diagnostic
import ipaddress
import shutil
from ruamel.yaml import YAML
from jinja2 import Environment, FileSystemLoader
from io import StringIO
import os, re
import sys
import enum

ruamel_yaml_default_mode = YAML()
ruamel_yaml_default_mode.width = 2048  # type: ignore

class ACTIONS(enum.Enum):
    ADD_WATCHER = "add_watcher"
    STOP_WATCHER = "stop_watcher"
    GET_STATUS = "get_status"
    DIAGNOSTIC = "diagnostic"


class WATCHER_CONFIG:
    P2P_VETH_SUPERNET_W_MASK = "169.254.0.0/16"
    WATCHER_ROOT_FOLDER = "watcher"
    WATCHER_TEMPLATE_FOLDER_NAME = "watcher-template"
    WATCHER_CONFIG_FILE = "config.yml"
    ROUTER_NODE_NAME = "router"
    ROUTER_ISIS_SYSTEMID = "{area_num}.{watcher_num}.{gre_num}.1111.00"
    WATCHER_NODE_NAME = "isis-watcher"
    ISIS_FILTER_NODE_NAME = "receive_only_filter"
    ISIS_FILTER_NODE_IMAGE = "vadims06/isis-filter-xdp:latest"

    def __init__(self, watcher_num, protocol="isis"):
        self.watcher_num = watcher_num
        # default
        self.gre_tunnel_network_device_ip = ""
        self.gre_tunnel_ip_w_mask_network_device = ""
        self.gre_tunnel_ip_w_mask_watcher = ""
        self.gre_tunnel_number = 0
        self.isis_area_num = ""
        self.host_interface_device_ip = ""
        self.protocol = protocol
        self.asn = 0
        self.organisation_name = ""
        self.watcher_name = ""

    def gen_next_free_number(self):
        """ Each Watcher installation has own sequense number starting from 1 """
        numbers = [int(folder_name.split('-')[0][-1]) for folder_name in WATCHER_CONFIG.get_existed_watchers() if '-' in folder_name]
        expected_numbers = set(range(1, max(numbers) + 1))
        if set(expected_numbers) == set(numbers):
            next_number = len(numbers) + 1
        else:
            next_number = next(iter(expected_numbers - set(numbers)))
        return next_number

    @staticmethod
    def get_existed_watchers():
        """ Return a list of watcher folders """
        watcher_root_folder_path = os.path.join(os.getcwd(), WATCHER_CONFIG.WATCHER_ROOT_FOLDER)
        return [file for file in os.listdir(watcher_root_folder_path) if os.path.isdir(os.path.join(watcher_root_folder_path, file)) and file.startswith("watcher") and not file.endswith("template")]

    def import_from(self, watcher_num):
        """
        Browse a folder directory and find a folder with watcher num. Parse GRE tunnel
        """
        # watcher1-gre1025-ospf
        watcher_re = re.compile("(?P<name>[a-zA-Z]+)(?P<watcher_num>\d+)-gre(?P<gre_num>\d+)(-(?P<proto>[a-zA-Z]+))?")
        for file in self.get_existed_watchers():
            watcher_match = watcher_re.match(file)
            if watcher_match and watcher_match.groupdict().get("watcher_num", "") == str(watcher_num):
                # these two attributes are needed to build paths
                self.protocol = watcher_match.groupdict().get("proto") if watcher_match.groupdict().get("proto") else self.protocol
                self.gre_tunnel_number = int(watcher_match.groupdict().get("gre_num", 0))
                for label, value in self.watcher_config_file_yml.get('topology', {}).get('defaults', {}).get('labels', {}).items():
                    setattr(self, label, value)
                break
        else:
            raise ValueError(f"Watcher{watcher_num} was not found")

    @property
    def p2p_veth_network_obj(self):
        """ ISIS p2p subnet assigment is top down: start from the end (255) to the start (0) in order not to overlap with OSPF """
        p2p_super_network_obj = ipaddress.ip_network(self.P2P_VETH_SUPERNET_W_MASK)
        return list(p2p_super_network_obj.subnets(new_prefix=24))[256 - self.watcher_num]

    @property
    def p2p_veth_watcher_ip_obj(self):
        return self.get_nth_elem_from_iter(self.p2p_veth_network_obj.hosts(), 2)

    @property
    def p2p_veth_watcher_ip_w_mask(self):
        return f"{str(self.p2p_veth_watcher_ip_obj)}/{self.p2p_veth_network_obj.prefixlen}"

    @property
    def p2p_veth_watcher_ip_w_slash_32_mask(self):
        return f"{str(self.p2p_veth_watcher_ip_obj)}/32"

    @property
    def p2p_veth_watcher_ip(self):
        return str(self.p2p_veth_watcher_ip_obj)

    @property
    def p2p_veth_host_ip_obj(self):
        return self.get_nth_elem_from_iter(self.p2p_veth_network_obj.hosts(), 1)

    @property
    def p2p_veth_host_ip_w_mask(self):
        return f"{str(self.p2p_veth_host_ip_obj)}/{self.p2p_veth_network_obj.prefixlen}"

    @property
    def host_veth(self):
        """ Add organisation name at name of interface to allow different interfaces with the same GRE num """
        linux_ip_link_peer_max_len = 15
        vhost_inf_name = f"vhost{self.gre_tunnel_number}"
        organisation_name_short = self.organisation_name[:linux_ip_link_peer_max_len - (len(vhost_inf_name)+1)] # 1 for dash
        self._host_veth = f"{organisation_name_short}-{vhost_inf_name}" if organisation_name_short else vhost_inf_name
        return self._host_veth

    @host_veth.setter
    def host_veth(self, value_from_yaml_import):
        self._host_veth = value_from_yaml_import

    @property
    def watcher_root_folder_path(self):
        return os.path.join(os.getcwd(), self.WATCHER_ROOT_FOLDER)

    @property
    def watcher_folder_name(self):
        return f"watcher{self.watcher_num}-gre{self.gre_tunnel_number}-{self.protocol}"

    @property
    def watcher_log_file_name(self):
        return f"{self.watcher_folder_name}.{self.protocol}.log"

    @property
    def watcher_folder_path(self):
        return os.path.join(self.watcher_root_folder_path, self.watcher_folder_name)

    @property
    def watcher_template_path(self):
        return os.path.join(self.watcher_root_folder_path, self.WATCHER_TEMPLATE_FOLDER_NAME)

    @property
    def router_template_path(self):
        return os.path.join(self.watcher_template_path, self.ROUTER_NODE_NAME)

    @property
    def router_folder_path(self):
        return os.path.join(self.watcher_folder_path, self.ROUTER_NODE_NAME)

    @property
    def watcher_config_file_path(self):
        return os.path.join(self.watcher_folder_path, self.WATCHER_CONFIG_FILE)

    @property
    def watcher_config_file_yml(self) -> dict:
        if os.path.exists(self.watcher_config_file_path):
            with open(self.watcher_config_file_path) as f:
                return ruamel_yaml_default_mode.load(f)
        return {}

    @property
    def watcher_config_template_yml(self):
        watcher_template_path = os.path.join(self.watcher_root_folder_path, self.WATCHER_TEMPLATE_FOLDER_NAME)
        with open(os.path.join(watcher_template_path, self.WATCHER_CONFIG_FILE)) as f:
            return ruamel_yaml_default_mode.load(f)

    @property
    def isis_watcher_template_path(self):
        return os.path.join(self.watcher_template_path, self.WATCHER_NODE_NAME)

    @property
    def isis_watcher_folder_path(self):
        return os.path.join(self.watcher_folder_path, self.WATCHER_NODE_NAME)
        
    @property
    def netns_name(self):
        watcher_config_yml = self.watcher_config_template_yml
        if not watcher_config_yml.get("prefix"):
            return f"clab-{self.watcher_folder_name}-{self.ROUTER_NODE_NAME}"
        elif watcher_config_yml["prefix"] == "__lab-name":
            return f"{self.watcher_folder_name}-{self.ROUTER_NODE_NAME}"
        elif watcher_config_yml["prefix"] != "":
            return f"{watcher_config_yml['prefix']}-{self.watcher_folder_name}-{self.ROUTER_NODE_NAME}"
        return self.ROUTER_NODE_NAME

    @staticmethod
    def do_check_ip(ip_address_w_mask):
        try:
            return str(ipaddress.ip_interface(ip_address_w_mask).ip)
        except:
            return ""

    @staticmethod
    def do_check_area_num(area_num):
        """ 49.xxxx """
        area_match = re.match('^49\.\d{4}$', area_num)
        return area_match.group(0) if area_match else ""

    @staticmethod
    def _get_digit_net_mask(ip_address_w_mask):
        return ipaddress.ip_interface(ip_address_w_mask).network.prefixlen

    @staticmethod
    def get_nth_elem_from_iter(iterator, number):
        while number > 0:
            value = iterator.__next__()
            number -= 1
        return value

    @staticmethod
    def is_network_the_same(ip_address_w_mask_1, ip_address_w_mask_2):
        return ipaddress.ip_interface(ip_address_w_mask_1).network == ipaddress.ip_interface(ip_address_w_mask_2).network

    def create_folder_with_settings(self):
        # watcher folder
        os.mkdir(self.watcher_folder_path)
        # isis-watcher folder
        watcher_logs_folder_path = os.path.join(self.watcher_root_folder_path, "logs")
        if not os.path.exists(watcher_logs_folder_path):
            os.mkdir(watcher_logs_folder_path)
        #os.mkdir(isis_watcher_folder_path)
        shutil.copyfile(
            src=os.path.join(self.isis_watcher_template_path, "watcher.log"),
            dst=os.path.join(watcher_logs_folder_path, self.watcher_log_file_name),
        )
        os.chmod(os.path.join(watcher_logs_folder_path, self.watcher_log_file_name), 0o755)
        # router folder inside watcher
        os.mkdir(self.router_folder_path)
        for file_name in ["daemons"]:
            shutil.copyfile(
                src=os.path.join(self.router_template_path, file_name),
                dst=os.path.join(self.router_folder_path, file_name)
            )
        # Config generation
        env = Environment(
            loader=FileSystemLoader(self.router_template_path)
        )
        # frr.conf
        frr_template = env.get_template("frr.template")
        frr_config = frr_template.render(
            system_id=self.ROUTER_ISIS_SYSTEMID.format(
                area_num=str(self.isis_area_num).zfill(4),
                watcher_num=str(self.watcher_num).zfill(4),
                gre_num=str(self.gre_tunnel_number).zfill(4),
            ),
            watcher_name=self.watcher_folder_name,
        )
        with open(os.path.join(self.router_folder_path, "frr.conf"), "w") as f:
            f.write(frr_config)
        # vtysh.conf
        vtysh_template = env.get_template("vtysh.template")
        vtysh_config = vtysh_template.render(watcher_name=self.watcher_folder_name)
        with open(os.path.join(self.router_folder_path, "vtysh.conf"), "w") as f:
            f.write(vtysh_config)
        # containerlab config
        watcher_config_yml = self.watcher_config_template_yml
        watcher_config_yml["name"] = self.watcher_folder_name
        # remember user input for further user, i.e diagnostic
        watcher_config_yml['topology']['defaults'].setdefault('labels', {}).update({'gre_num': int(self.gre_tunnel_number)})
        watcher_config_yml['topology']['defaults']['labels'].update({'gre_tunnel_network_device_ip': self.gre_tunnel_network_device_ip})
        watcher_config_yml['topology']['defaults']['labels'].update({'gre_tunnel_ip_w_mask_network_device': self.gre_tunnel_ip_w_mask_network_device})
        watcher_config_yml['topology']['defaults']['labels'].update({'gre_tunnel_ip_w_mask_watcher': self.gre_tunnel_ip_w_mask_watcher})
        watcher_config_yml['topology']['defaults']['labels'].update({'area_num': self.isis_area_num})
        watcher_config_yml['topology']['defaults']['labels'].update({'asn': self.asn})
        watcher_config_yml['topology']['defaults']['labels'].update({'organisation_name': self.organisation_name})
        watcher_config_yml['topology']['defaults']['labels'].update({'watcher_name': self.watcher_name})
        # Config
        watcher_config_yml['topology']['nodes']['h1']['exec'] = self.exec_cmds()
        watcher_config_yml['topology']['links'] = [{'endpoints': [f'{self.ROUTER_NODE_NAME}:veth1', f'host:{self.host_veth}']}]
        # Watcher
        watcher_config_yml['topology']['nodes'][self.WATCHER_NODE_NAME]['network-mode'] = f"container:{self.ROUTER_NODE_NAME}"
        watcher_config_yml['topology']['nodes'][self.WATCHER_NODE_NAME]['binds'].append(f"../logs/{self.watcher_log_file_name}:/home/watcher/watcher/logs/watcher.log")
        watcher_config_yml['topology']['nodes'][self.WATCHER_NODE_NAME].update({'env': {'ASN': self.asn}})
        watcher_config_yml['topology']['nodes'][self.WATCHER_NODE_NAME]['env'].update({'WATCHER_NAME': self.watcher_name})
        watcher_config_yml['topology']['nodes'][self.WATCHER_NODE_NAME]['env'].update({'AREA_NUM': self.isis_area_num})
        watcher_config_yml['topology']['nodes'][self.WATCHER_NODE_NAME]['env'].update({'WATCHER_INTERFACE': "veth1"})
        watcher_config_yml['topology']['nodes'][self.WATCHER_NODE_NAME]['env'].update({'WATCHER_LOGFILE': "/home/watcher/watcher/logs/watcher.log"})
        # IS-IS XDP filter, listen only
        watcher_config_yml['topology']['nodes'][self.ISIS_FILTER_NODE_NAME]['image'] = self.ISIS_FILTER_NODE_IMAGE
        watcher_config_yml['topology']['nodes'][self.ISIS_FILTER_NODE_NAME]['network-mode'] = "host"
        watcher_config_yml['topology']['nodes'][self.ISIS_FILTER_NODE_NAME]['env']['VTAP_HOST_INTERFACE'] = self.host_veth
        # Enable GRE after XDP filter
        watcher_config_yml['topology']['nodes']['h2']['exec'] = [f'sudo ip netns exec {self.netns_name} ip link set up dev gre1']
        with open(os.path.join(self.watcher_folder_path, "config.yml"), "w") as f:
            s = StringIO()
            ruamel_yaml_default_mode.dump(watcher_config_yml, s)
            f.write(s.getvalue())

    def do_add_watcher_prechecks(self):
        if os.path.exists(self.watcher_folder_path):
            raise ValueError(f"Watcher{self.watcher_num} with GRE{self.gre_tunnel_number} already exists")
        # TODO, check if GRE with the same tunnel destination already exist

    @staticmethod
    def do_print_banner():
        print("""
+---------------------------+                                        
|  Watcher Host             |                       +-------------------+
|  +------------+           |                       | Network device    |
|  | netns FRR  |           |                       |                   |
|  |            Tunnel [4]  |                       | Tunnel [4]        |
|  |  gre1   [3]TunnelIP----+-----------------------+[2]TunnelIP        |
|  |  eth1------+-vhost1    |       +-----+         | IS-IS area num [5]|
|  |            | Host IP[6]+-------+ LAN |--------[1]Device IP         |
|  |            |           |       +-----+         |                   |
|  +------------+           |                       |                   |
|                           |                       +-------------------+
+---------------------------+                                        
        """)

    def add_watcher_dialog(self):
        while not self.gre_tunnel_network_device_ip:
            self.gre_tunnel_network_device_ip = self.do_check_ip(input("[1]Network device IP [x.x.x.x]: "))
        while not self.gre_tunnel_ip_w_mask_network_device:
            self.gre_tunnel_ip_w_mask_network_device = input("[2]GRE Tunnel IP on network device with mask [x.x.x.x/yy]: ")
            if not self.do_check_ip(self.gre_tunnel_ip_w_mask_network_device):
                print("IP address is not correct")
                self.gre_tunnel_ip_w_mask_network_device = ""
            elif self._get_digit_net_mask(self.gre_tunnel_ip_w_mask_network_device) == 32:
                print("Please provide non /32 subnet for tunnel network")
                self.gre_tunnel_ip_w_mask_network_device = ""
            elif self.gre_tunnel_ip_w_mask_network_device == self.gre_tunnel_network_device_ip:
                print("Tunnel IP address shouldn't be the same as physical device IP address")
                self.gre_tunnel_ip_w_mask_network_device = ""
        while not self.gre_tunnel_ip_w_mask_watcher:
            self.gre_tunnel_ip_w_mask_watcher = input("[3]GRE Tunnel IP on Watcher with mask [x.x.x.x/yy]: ")
            if not self.do_check_ip(self.gre_tunnel_ip_w_mask_watcher):
                print("IP address is not correct")
                self.gre_tunnel_ip_w_mask_watcher = ""
            elif self._get_digit_net_mask(self.gre_tunnel_ip_w_mask_watcher) == 32:
                print("Please provide non /32 subnet for tunnel network")
                self.gre_tunnel_ip_w_mask_watcher = ""
            elif not self.is_network_the_same(self.gre_tunnel_ip_w_mask_network_device, self.gre_tunnel_ip_w_mask_watcher):
                print("Tunnel's network doesn't match")
                self.gre_tunnel_ip_w_mask_watcher = ""
            elif self.gre_tunnel_ip_w_mask_network_device == self.gre_tunnel_ip_w_mask_watcher:
                print("Tunnel' IP addresses must be different on endpoints")
                self.gre_tunnel_ip_w_mask_watcher = ""
        while not self.gre_tunnel_number:
            self.gre_tunnel_number = input("[4]GRE Tunnel number: ")
            if not self.gre_tunnel_number.isdigit():
                print("Please provide any positive number")
                self.gre_tunnel_number = ""
        # ISIS settings
        while not self.isis_area_num:
            self.isis_area_num = self.do_check_area_num(input("[5]IS-IS area number [49.xxxx]: "))
        # Host interface name for NAT
        while not self.host_interface_device_ip:
            self.host_interface_device_ip = self.do_check_ip(input("[6]Watcher host IP address: "))
        # Tags
        self.asn = input("AS number, where IS-IS is configured: ")
        if not self.asn and not self.asn.isdigit():
            self.asn = 0
        self.organisation_name = str(input("Organisation name: ")).lower()
        self.watcher_name = str(input("watcher name: ")).lower().replace(" ", "-")
        if not self.watcher_name:
            self.watcher_name = "isiswatcher-demo"
    
    def exec_cmds(self):
        return [
            f'ip netns exec {self.netns_name} ip address add {self.p2p_veth_watcher_ip_w_mask} dev veth1',
            f'ip netns exec {self.netns_name} ip route add {self.gre_tunnel_network_device_ip} via {str(self.p2p_veth_host_ip_obj)}',
            f'ip address add {self.p2p_veth_host_ip_w_mask} dev {self.host_veth}',
            f'ip netns exec {self.netns_name} ip tunnel add gre1 mode gre local {str(self.p2p_veth_watcher_ip_obj)} remote {self.gre_tunnel_network_device_ip}',
            f'ip netns exec {self.netns_name} ip address add {self.gre_tunnel_ip_w_mask_watcher} dev gre1',
            f'bash -c \'RULE="-t nat -p gre -s {self.p2p_veth_watcher_ip} -d {self.gre_tunnel_network_device_ip} -j SNAT --to-source {self.host_interface_device_ip}"; sudo iptables -C POSTROUTING $$RULE &> /dev/null && echo "Rule exists in iptables." || sudo iptables -A POSTROUTING $$RULE\'',
            f'bash -c \'RULE="-t nat -p gre -s {self.gre_tunnel_network_device_ip} -d {self.host_interface_device_ip} -j DNAT --to-destination {self.p2p_veth_watcher_ip}"; sudo iptables -C PREROUTING $$RULE &> /dev/null && echo "Rule exists in iptables." || sudo iptables -A PREROUTING $$RULE\'',
            f'bash -c \'RULE="-t filter -p gre -s {self.p2p_veth_watcher_ip} -d {self.gre_tunnel_network_device_ip} -i {self.host_veth} -j ACCEPT"; sudo iptables -C FORWARD $$RULE &> /dev/null && echo "Rule exists in iptables." || sudo iptables -A FORWARD $$RULE\'',
            f'bash -c \'RULE="-t filter -p gre -s {self.gre_tunnel_network_device_ip} -j ACCEPT"; sudo iptables -C FORWARD $$RULE &> /dev/null && echo "Rule exists in iptables." || sudo iptables -A FORWARD $$RULE\'',
            f'sudo ip netns exec {self.netns_name} ip link set mtu 1600 dev veth1', # for xdp
            # enable GRE after applying XDP filter
            #f'sudo ip netns exec {self.netns_name} ip link set up dev gre1',
            f'sudo ip link set mtu 1600 dev {self.host_veth}',
            f'sudo conntrack -D --dst {self.gre_tunnel_network_device_ip} -p 47',
            f'sudo conntrack -D --src {self.gre_tunnel_network_device_ip} -p 47',
        ]

    @classmethod
    def parse_command_args(cls, args):
        allowed_actions = [actions.value for actions in ACTIONS]
        if args.action not in allowed_actions:
            raise ValueError(f"Not allowed action. Supported actions: {', '.join(allowed_actions)}")
        watcher_num = args.watcher_num if args.watcher_num else len( cls.get_existed_watchers() ) + 1
        watcher_obj = cls(watcher_num)
        watcher_obj.run_command(args.action)

    def run_command(self, action):
        method = getattr(self, action)
        return method()

    def add_watcher(self):
        self.do_print_banner()
        self.add_watcher_dialog()
        self.do_add_watcher_prechecks()
        # create folder
        self.create_folder_with_settings()
        print(f"Config has been successfully generated!")

    def stop_watcher(self):
        raise NotImplementedError("Not implemented yet. Please run manually `sudo clab destroy --topo <path to config.yml>`")

    def get_status(self):
        # TODO add IS-IS neighborship status
        raise NotImplementedError("Not implemented yet. Please run manually `sudo docker ps -f label=clab-node-name=router`")

    def diagnostic(self):
        print(f"Diagnostic connection is started")
        self.import_from(watcher_num=args.watcher_num)
        diag_watcher_host = diagnostic.WATCHER_HOST(
            if_names=[self.host_veth],
            watcher_internal_ip=self.p2p_veth_watcher_ip,
            network_device_ip=self.gre_tunnel_network_device_ip
        )
        diag_watcher_host.does_conntrack_exist_for_gre()
        # print(f"Please wait {diag_watcher_host.DUMP_FILTER_TIMEOUT} sec")
        diag_watcher_host.run()
        if not diagnostic.IPTABLES_NAT_FOR_REMOTE_NETWORK_DEVICE_UNIQUE.check(self.gre_tunnel_network_device_ip):
            sys.exit()
        if diag_watcher_host.is_watcher_alive:
            diagnostic.IPTABLES_FRR_NETNS_FORWARD_TO_NETWORK_DEVICE_BEFORE_NAT.check(self.gre_tunnel_network_device_ip)
        if diag_watcher_host.is_network_device_alive:
            diagnostic.IPTABLES_REMOTE_NETWORK_DEVICE_FORWARD_TO_FRR_NETNS.check(self.gre_tunnel_network_device_ip)
            diagnostic.IPTABLES_REMOTE_NETWORK_DEVICE_NAT_TO_FRR_NETNS.check(self.gre_tunnel_network_device_ip)



if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description="Provisioning Watcher instances for tracking IS-IS topology changes"
    )
    parser.add_argument(
        "--action", required=True, help="Options: add_watcher, stop_watcher, get_status, diagnostic"
    )
    parser.add_argument(
        "--watcher_num", required=False, default=0, type=int, help="Number of watcher"
    )
    
    args = parser.parse_args()
    allowed_actions = [actions.value for actions in ACTIONS]
    if args.action not in allowed_actions:
        raise ValueError(f"Not allowed action. Supported actions: {', '.join(allowed_actions)}")
    watcher_conf = WATCHER_CONFIG.parse_command_args(args)
