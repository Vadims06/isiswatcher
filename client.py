import argparse
import copy
import enum
import ipaddress
import os
import re
import shutil
import warnings
from io import StringIO
import requests
import sys
from jinja2 import Environment, FileSystemLoader
from ruamel.yaml import YAML

try:
    from cryptography.utils import CryptographyDeprecationWarning
    warnings.filterwarnings("ignore", category=CryptographyDeprecationWarning)
except ImportError:
    warnings.filterwarnings("ignore", category=DeprecationWarning, module="cryptography")

import diagnostic

ruamel_yaml_default_mode = YAML()
ruamel_yaml_default_mode.width = 2048  # type: ignore

class ACTIONS(enum.Enum):
    ADD_WATCHER = "add_watcher"
    DIAGNOSTIC = "diagnostic"
    ENABLE_XDP = "enable_xdp"
    DISABLE_XDP = "disable_xdp"


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
    LOGROTATION_NODE_NAME = "logrotation"
    LOGROTATION_IMAGE = "vadims06/docker-logrotate:v1.0.0"
    BGPLSWATCHER_NODE_NAME = "bgplswatcher"
    BGPLSWATCHER_IMAGE = "vadims06/bgplswatcher:latest"

    def __init__(self, watcher_num, protocol="isis"):
        self.watcher_num = watcher_num
        # default
        self.connection_mode = "gre"  # "gre" or "bgpls"
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
        self.enable_xdp = False
        self.enable_topolograph = False
        # BGP-LS specific attributes
        self.bgpls_router_ip = ""
        self.bgpls_router_as = 0
        self.bgpls_watcher_as = 0
        self.bgpls_router_id = ""
        self.bgpls_passive_mode = False
        self.bgpls_listen_port = 50051
        self.bgpls_grpc_port = 0

    def gen_next_free_number():
        """ Each Watcher installation has own sequence number starting from 1 """
        watcher_seq_numbers = [int(folder_name.split('-')[0][7:]) for folder_name in WATCHER_CONFIG.get_existed_watchers() if '-' in folder_name]
        if not watcher_seq_numbers:
            return 1
        expected_numbers = set(range(1, max(watcher_seq_numbers) + 1))
        if set(expected_numbers) == set(watcher_seq_numbers):
            next_number = len(watcher_seq_numbers) + 1
        else:
            next_number = next(iter(expected_numbers - set(watcher_seq_numbers)))
        return next_number

    @staticmethod
    def get_existed_watchers():
        """ Return a list of watcher folders """
        watcher_root_folder_path = os.path.join(os.getcwd(), WATCHER_CONFIG.WATCHER_ROOT_FOLDER)
        return [file for file in os.listdir(watcher_root_folder_path) if os.path.isdir(os.path.join(watcher_root_folder_path, file)) and file.startswith("watcher") and not file.endswith("template")]

    def import_from(self, watcher_num):
        """
        Browse a folder directory and find a folder with watcher num. Parse GRE tunnel or BGP-LS
        """
        # watcher1-gre1025-ospf or watcher1-bgpls-isis
        watcher_re_gre = re.compile(r"(?P<name>[a-zA-Z]+)(?P<watcher_num>\d+)-gre(?P<gre_num>\d+)(-(?P<proto>[a-zA-Z]+))?")
        watcher_re_bgpls = re.compile(r"(?P<name>[a-zA-Z]+)(?P<watcher_num>\d+)-bgpls(-(?P<proto>[a-zA-Z]+))?")
        for file in self.get_existed_watchers():
            watcher_match_gre = watcher_re_gre.match(file)
            watcher_match_bgpls = watcher_re_bgpls.match(file)
            watcher_match = watcher_match_gre or watcher_match_bgpls
            if watcher_match and watcher_match.groupdict().get("watcher_num", "") == str(watcher_num):
                # these two attributes are needed to build paths
                self.protocol = watcher_match.groupdict().get("proto") if watcher_match.groupdict().get("proto") else self.protocol
                if watcher_match_gre:
                    self.connection_mode = "gre"
                    self.gre_tunnel_number = int(watcher_match.groupdict().get("gre_num", 0))
                elif watcher_match_bgpls:
                    self.connection_mode = "bgpls"
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
        if self.connection_mode == "bgpls":
            return f"watcher{self.watcher_num}-bgpls-{self.protocol}"
        else:
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
    def bgplswatcher_folder_path(self):
        return os.path.join(self.watcher_folder_path, self.BGPLSWATCHER_NODE_NAME)
        
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
        area_match = re.match(r'^49\.\d{4}$', area_num)
        return area_match.group(0) if area_match else ""

    @staticmethod
    def _get_digit_net_mask(ip_address_w_mask):
        return ipaddress.ip_interface(ip_address_w_mask).network.prefixlen

    def _add_topolograph_host_to_env(self):
        # Create .env from .env.template if it doesn't exist
        if not os.path.exists('.env'):
            if os.path.exists('.env.template'):
                shutil.copyfile('.env.template', '.env')
                print("Created .env file from .env.template\n")
            else:
                raise FileNotFoundError(".env file not found and .env.template is missing. Please create .env file manually.")
        # open local .env file and replace TOPOLOGRAPH_HOST env
        with open('.env', 'r') as f:
            lines = f.readlines()
        with open('.env', 'w') as f:
            for line in lines:
                if line.startswith('TOPOLOGRAPH_HOST'):
                    f.write(f'TOPOLOGRAPH_HOST={self.host_interface_device_ip}\n')
                    print(f"TOPOLOGRAPH_HOST set to {self.host_interface_device_ip} in .env\n")
                elif line.startswith('WEBHOOK_URL'):
                    f.write(f'WEBHOOK_URL={self.host_interface_device_ip}\n')
                    print(f"WEBHOOK_URL set to {self.host_interface_device_ip} in .env\n")
                else:
                    f.write(line)

    def do_check_topolograph_availability(self):
        from dotenv import load_dotenv
        load_dotenv()
        # using TOPOLOGRAPH_* env variable check if get request is ok
        try:
            _login, _pass = os.getenv('TOPOLOGRAPH_WEB_API_USERNAME_EMAIL', ''), os.getenv('TOPOLOGRAPH_WEB_API_PASSWORD', '')
            _host, _port = os.getenv('TOPOLOGRAPH_HOST', ''), os.getenv('TOPOLOGRAPH_PORT', '')
            r_get = requests.get(f'http://{_host}:{_port}/api/graph/', auth=(_login, _pass), timeout=(5, 30))
            status_name = 'ok' if r_get.ok or r_get.status_code == 404 else 'bad'
            print(f"Access to {_host}:{_port} is {status_name}")
            if r_get.status_code != 200 and r_get.status_code != 404:
                print(f"Access to {_host}:{_port} is {r_get.status_code} error, details: {r_get.text}")
            return r_get.ok
        except Exception as e:
            print(f"Warning: Could not check Topolograph availability: {e}")
            print("Continuing with watcher setup...")
            return False

    @staticmethod
    def get_nth_elem_from_iter(iterator, number):
        while number > 0:
            value = iterator.__next__()
            number -= 1
        return value

    @staticmethod
    def is_network_the_same(ip_address_w_mask_1, ip_address_w_mask_2):
        return ipaddress.ip_interface(ip_address_w_mask_1).network == ipaddress.ip_interface(ip_address_w_mask_2).network

    def generate_bgplswatcher_config(self):
        """Generate config.toml for bgplswatcher (GoBGP)"""
        config_toml_path = os.path.join(self.bgplswatcher_folder_path, "config.toml")
        # topolograph-watcher-endpoint format: localhost:port (both containers use host network mode)
        topolograph_endpoint = f"localhost:{self.bgpls_grpc_port}"
        
        config_content = f"""[global.config]
  as = {self.bgpls_watcher_as}
  router-id = "{self.bgpls_router_id}"
  topolograph-watcher-endpoint = "{topolograph_endpoint}"

[[neighbors]]
  [neighbors.config]
    neighbor-address = "{self.bgpls_router_ip}"
    peer-as = {self.bgpls_router_as}
    topolograph-watcher-endpoint = "{topolograph_endpoint}"
"""
        if self.bgpls_passive_mode:
            config_content += "    passive-mode = true\n"
        
        # Add eBGP multihop config if enabled
        if self.bgpls_ebgp_multihop:
            config_content += """  [neighbors.ebgp-multihop.config]
    enabled = true
    multihop-ttl = 255
"""
        
        with open(config_toml_path, "w") as f:
            f.write(config_content)

    def create_folder_with_settings(self):
        if self.connection_mode == "bgpls":
            self.create_folder_with_settings_bgpls()
            return
        # GRE mode (existing implementation)
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
        watcher_config_yml['topology']['nodes'][self.WATCHER_NODE_NAME]['env'].update({'PEER_ID': self.gre_tunnel_network_device_ip}) # for BGP-LS backward compatibility
        # Logrotation
        watcher_config_yml['topology']['nodes'][self.LOGROTATION_NODE_NAME]['image'] = self.LOGROTATION_IMAGE
        watcher_config_yml['topology']['nodes'][self.LOGROTATION_NODE_NAME].setdefault('binds', []).append(f"../logs/{self.watcher_log_file_name}:/logs/watcher.log")
        # IS-IS XDP filter, listen only
        if self.enable_xdp:
            watcher_config_yml['topology']['nodes'][self.ISIS_FILTER_NODE_NAME]['image'] = self.ISIS_FILTER_NODE_IMAGE
            watcher_config_yml['topology']['nodes'][self.ISIS_FILTER_NODE_NAME]['network-mode'] = "host"
            watcher_config_yml['topology']['nodes'][self.ISIS_FILTER_NODE_NAME]['env']['VTAP_HOST_INTERFACE'] = self.host_veth
        else:
            del watcher_config_yml['topology']['nodes'][self.ISIS_FILTER_NODE_NAME]
            for d in watcher_config_yml['topology']['nodes']['h2']['stages']['create']['wait-for']:
                if d.get("node") == self.ISIS_FILTER_NODE_NAME:
                    d['node'] = 'h1'
        # Enable GRE after XDP filter
        watcher_config_yml['topology']['nodes']['h2']['exec'] = [f'sudo ip netns exec {self.netns_name} ip link set up dev gre1']
        self._do_save_watcher_config_file(watcher_config_yml)

    def create_folder_with_settings_bgpls(self):
        """Create folder structure and config for BGP-LS mode"""
        # watcher folder
        os.mkdir(self.watcher_folder_path)
        # logs folder
        watcher_logs_folder_path = os.path.join(self.watcher_root_folder_path, "logs")
        if not os.path.exists(watcher_logs_folder_path):
            os.mkdir(watcher_logs_folder_path)
        # Create log file
        shutil.copyfile(
            src=os.path.join(self.isis_watcher_template_path, "watcher.log"),
            dst=os.path.join(watcher_logs_folder_path, self.watcher_log_file_name),
        )
        os.chmod(os.path.join(watcher_logs_folder_path, self.watcher_log_file_name), 0o755)
        # bgplswatcher folder
        os.mkdir(self.bgplswatcher_folder_path)
        # Generate bgplswatcher config.toml
        self.generate_bgplswatcher_config()
        # Generate containerlab config.yml
        watcher_config_yml = copy.deepcopy(self.watcher_config_template_yml)
        watcher_config_yml["name"] = self.watcher_folder_name
        # Remove nodes that are not needed for BGP-LS
        if 'router' in watcher_config_yml['topology']['nodes']:
            del watcher_config_yml['topology']['nodes']['router']
        if 'h1' in watcher_config_yml['topology']['nodes']:
            del watcher_config_yml['topology']['nodes']['h1']
        if 'h2' in watcher_config_yml['topology']['nodes']:
            del watcher_config_yml['topology']['nodes']['h2']
        if self.ISIS_FILTER_NODE_NAME in watcher_config_yml['topology']['nodes']:
            del watcher_config_yml['topology']['nodes'][self.ISIS_FILTER_NODE_NAME]
        # Remove links section
        if 'links' in watcher_config_yml['topology']:
            del watcher_config_yml['topology']['links']
        # Store BGP-LS config in labels
        watcher_config_yml['topology']['defaults'].setdefault('labels', {}).update({
            'connection_mode': 'bgpls',
            'bgpls_router_ip': self.bgpls_router_ip,
            'bgpls_router_as': self.bgpls_router_as,
            'bgpls_watcher_as': self.bgpls_watcher_as,
            'bgpls_router_id': self.bgpls_router_id,
            'bgpls_passive_mode': self.bgpls_passive_mode,
            'bgpls_grpc_port': self.bgpls_grpc_port,
            'area_num': self.isis_area_num,
            'asn': self.asn,
            'organisation_name': self.organisation_name,
            'watcher_name': self.watcher_name,
        })
        # bgplswatcher node
        watcher_config_yml['topology']['nodes'][self.BGPLSWATCHER_NODE_NAME] = {
            'kind': 'linux',
            'image': self.BGPLSWATCHER_IMAGE,
            'binds': [
                f'bgplswatcher/config.toml:/app/config.toml:ro'
            ],
        }
        if self.bgpls_passive_mode:
            watcher_config_yml['topology']['nodes'][self.BGPLSWATCHER_NODE_NAME].setdefault('ports', []).append('179:179')
        # isis-watcher node (BGP mode)
        # Ensure we have a clean node structure
        watcher_node = watcher_config_yml['topology']['nodes'][self.WATCHER_NODE_NAME]
        watcher_node['image'] = 'vadims06/isis-watcher:latest'
        watcher_node['network-mode'] = f"container:{self.BGPLSWATCHER_NODE_NAME}"
        # Remove any existing entrypoint/cmd to avoid conflicts
        if 'entrypoint' in watcher_node:
            del watcher_node['entrypoint']
        if 'cmd' in watcher_node:
            del watcher_node['cmd']
        # Set cmd - containerlab uses cmd to override container command
        # Note: This will be passed as arguments to the existing entrypoint
        # So we need to set entrypoint to the script and cmd to the argument
        watcher_node['entrypoint'] = '/entrypoint.sh'
        watcher_node['cmd'] = 'bgp'
        watcher_node['binds'] = [f"../logs/{self.watcher_log_file_name}:/home/watcher/watcher/logs/watcher.log"]
        watcher_node['env'] = {
            'BGP_GRPC_PORT': str(self.bgpls_grpc_port),
            'WATCHER_LOGFILE': '/home/watcher/watcher/logs/watcher.log',
            'ASN': self.asn,
            'WATCHER_NAME': self.watcher_name,
            'AREA_NUM': self.isis_area_num,
        }
        # Remove stages if they reference deleted nodes
        if 'stages' in watcher_config_yml['topology']['nodes'][self.WATCHER_NODE_NAME]:
            stages = watcher_config_yml['topology']['nodes'][self.WATCHER_NODE_NAME]['stages']
            if 'create' in stages and 'wait-for' in stages['create']:
                stages['create']['wait-for'] = [
                    wait_item for wait_item in stages['create']['wait-for']
                    if wait_item.get('node') not in ['h1', 'h2', 'router', self.ISIS_FILTER_NODE_NAME]
                ]
        # logrotation node
        watcher_config_yml['topology']['nodes'][self.LOGROTATION_NODE_NAME]['image'] = self.LOGROTATION_IMAGE
        watcher_config_yml['topology']['nodes'][self.LOGROTATION_NODE_NAME].setdefault('binds', []).append(f"../logs/{self.watcher_log_file_name}:/logs/watcher.log")
        self._do_save_watcher_config_file(watcher_config_yml)

    def _do_save_watcher_config_file(self, _config):
        with open(self.watcher_config_file_path, "w") as f:
            s = StringIO()
            ruamel_yaml_default_mode.dump(_config, s)
            f.write(s.getvalue())

    def do_add_watcher_prechecks(self):
        if os.path.exists(self.watcher_folder_path):
            if self.connection_mode == "bgpls":
                raise ValueError(f"Watcher{self.watcher_num} with BGP-LS already exists")
            else:
                raise ValueError(f"Watcher{self.watcher_num} with GRE{self.gre_tunnel_number} already exists")
        # TODO, check if GRE with the same tunnel destination already exist without root access

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

    @staticmethod
    def do_print_banner_bgpls():
        print("""
+-------------------------------+                                        
|  Watcher Host                 |                       +-------------------+
|  +-------------------+        |                       | Network Router    |
|  | bgplswatcher[3][4]|        |                       |                   |
|  | (GoBGP)           |<-------+BGP Session [1][2]---+ | BGP Session [1][2]|
|  |      [gRPC]       |        |                       |    ^              |
|  |        |          |        |                       |    |              |
|  |        v          |        |                       | BGP-LS            |
|  | isis-watcher      |        |                       |    ^              |
|  | (Python)          |        |                       |    |              |
|  |                   |        |                       | IS-IS [5]         |
|  +-------------------+        |                       |                   |
|                               |                       +-------------------+
+-------------------------------+                                            
        """)

    def add_watcher_dialog_bgpls(self):
        # Router IP address (BGP peer)
        while not self.bgpls_router_ip:
            self.bgpls_router_ip = self.do_check_ip(input("[1]Router IP address (BGP peer) [x.x.x.x]: "))
        # Router AS number
        while not self.bgpls_router_as:
            router_as_input = input("[2]Router AS number: ")
            if router_as_input.isdigit():
                self.bgpls_router_as = int(router_as_input)
            else:
                print("Please provide a valid AS number")
        # Watcher AS number
        while not self.bgpls_watcher_as:
            watcher_as_input = input("[3]Watcher AS number: ")
            if watcher_as_input.isdigit():
                self.bgpls_watcher_as = int(watcher_as_input)
            else:
                print("Please provide a valid AS number")
        # Check if AS numbers don't match (eBGP) and ask about multihop
        self.bgpls_ebgp_multihop = False
        if self.bgpls_router_as and self.bgpls_watcher_as and self.bgpls_router_as != self.bgpls_watcher_as:
            self.bgpls_ebgp_multihop = True
            print("⚠ eBGP multihop was enabled with TTL 255. Don't forget to configure it on the router side")
        # Watcher router-id
        while not self.bgpls_router_id:
            self.bgpls_router_id = self.do_check_ip(input("[4]Watcher router-id [x.x.x.x]: "))
        # ISIS settings (for labeling/logging)
        while not self.isis_area_num:
            self.isis_area_num = self.do_check_area_num(input("[5]IS-IS area number [49.xxxx]: "))
        # Passive mode
        self.bgpls_passive_mode = None
        while self.bgpls_passive_mode is None:
            passive_mode_reply = input("[6]Passive mode? [y/N] ")
            if not passive_mode_reply:
                self.bgpls_passive_mode = False
            else:
                if passive_mode_reply.lower().strip() == 'y':
                    self.bgpls_passive_mode = True
                elif passive_mode_reply.lower().strip() == 'n':
                    self.bgpls_passive_mode = False
                    print("⚠ Passive mode disabled - it limits the number of goBGP instances running on the same host to 1")
                    print("⚠ You can run multiple goBGP instances on the same host by disabling passive mode")
        # Calculate gRPC port based on protocol
        if self.protocol == "isis":
            self.bgpls_grpc_port = 50100 + self.watcher_num
        elif self.protocol == "ospf":
            self.bgpls_grpc_port = 50200 + self.watcher_num
        else:
            self.bgpls_grpc_port = 50100 + self.watcher_num  # Default to ISIS port
        # Topolograph's IP settings
        self.enable_topolograph = None
        while self.enable_topolograph is None:
            enable_topolograph_reply = input("Enable Topolograph? [Y/n] ")
            if not enable_topolograph_reply:
                self.enable_topolograph = True
            else:
                if enable_topolograph_reply.lower().strip() == 'y':
                    self.enable_topolograph = True
                elif enable_topolograph_reply.lower().strip() == 'n':
                    self.enable_topolograph = False
        if self.enable_topolograph:
            # For BGP-LS, we need host IP for Topolograph
            while not self.host_interface_device_ip:
                self.host_interface_device_ip = self.do_check_ip(input("Watcher host IP address: "))
            self._add_topolograph_host_to_env()
            self.do_check_topolograph_availability()
        # Tags
        asn_default = self.bgpls_watcher_as if self.bgpls_watcher_as else 0
        asn_input = input(f"AS number, where IS-IS is configured: [{asn_default}] ")
        if not asn_input:
            self.asn = asn_default
        elif asn_input.isdigit():
            self.asn = int(asn_input)
        else:
            self.asn = asn_default
        self.organisation_name = str(input("Organisation name: ")).lower()
        self.watcher_name = str(input("Watcher name: ")).lower().replace(" ", "-")
        if not self.watcher_name:
            self.watcher_name = "isiswatcher-demo"

    def add_watcher_dialog(self):
        # Connection mode should already be set by add_watcher
        if self.connection_mode == "bgpls":
            self.add_watcher_dialog_bgpls()
            return
        
        # GRE mode dialog (existing)
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
        # Topolograph's IP settings
        self.enable_topolograph = None
        while self.enable_topolograph is None:
            enable_topolograph_reply = input("Enable Topolograph? [Y/n] ")
            if not enable_topolograph_reply:
                self.enable_topolograph = True
            else:
                if enable_topolograph_reply.lower().strip() == 'y':
                    self.enable_topolograph = True
                elif enable_topolograph_reply.lower().strip() == 'n':
                    self.enable_topolograph = False
        if self.enable_topolograph:
            self._add_topolograph_host_to_env()
            self.do_check_topolograph_availability()
        # Tags
        self.asn = input("AS number, where IS-IS is configured: [0]")
        if not self.asn and not self.asn.isdigit():
            self.asn = 0
        self.organisation_name = str(input("Organisation name: ")).lower()
        self.watcher_name = str(input("Watcher name: ")).lower().replace(" ", "-")
        if not self.watcher_name:
            self.watcher_name = "isiswatcher-demo"
        self.enable_xdp = None
        while self.enable_xdp is None:
            enable_xdp_reply = input("Enable XDP? [y/N] ")
            if not enable_xdp_reply:
                self.enable_xdp = False
            else:
                if enable_xdp_reply.lower().strip() == 'y':
                    self.enable_xdp = True
                elif enable_xdp_reply.lower().strip() == 'n':
                    self.enable_xdp = False
    
    def exec_cmds(self):
        return [
            f'ip netns exec {self.netns_name} ip address add {self.p2p_veth_watcher_ip_w_mask} dev veth1',
            f'ip netns exec {self.netns_name} ip route add {self.gre_tunnel_network_device_ip} via {str(self.p2p_veth_host_ip_obj)}',
            f'ip address add {self.p2p_veth_host_ip_w_mask} dev {self.host_veth}',
            f'ip netns exec {self.netns_name} ip tunnel add gre1 mode gre local {str(self.p2p_veth_watcher_ip_obj)} remote {self.gre_tunnel_network_device_ip} ttl 100',
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
        # Ask for connection mode first to show correct banner
        connection_mode_input = None
        while connection_mode_input not in ["gre", "bgpls"]:
            connection_mode_input = input("Connection mode (gre/bgpls): [gre] ").lower().strip()
            if not connection_mode_input:
                connection_mode_input = "gre"
            if connection_mode_input not in ["gre", "bgpls"]:
                print("Please enter 'gre' or 'bgpls'")
        self.connection_mode = connection_mode_input
        
        # Show appropriate banner
        if self.connection_mode == "bgpls":
            self.do_print_banner_bgpls()
        else:
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
        diagnostic.IPTABLES_NAT_FOR_REMOTE_NETWORK_DEVICE_UNIQUE.check(self.gre_tunnel_network_device_ip)
        if diag_watcher_host.is_watcher_alive:
            diagnostic.IPTABLES_FRR_NETNS_FORWARD_TO_NETWORK_DEVICE_BEFORE_NAT.check(self.gre_tunnel_network_device_ip)
        if diag_watcher_host.is_network_device_alive:
            is_passed = diagnostic.IPTABLES_REMOTE_NETWORK_DEVICE_FORWARD_TO_FRR_NETNS.check(self.gre_tunnel_network_device_ip)
            if not is_passed:
                diagnostic.IPTABLES_REMOTE_NETWORK_DEVICE_NAT_TO_FRR_NETNS.check(self.gre_tunnel_network_device_ip)

    def enable_xdp(self):
        self.import_from(watcher_num=args.watcher_num)
        current_clab_config = self.watcher_config_file_yml
        if not current_clab_config:
            raise ValueError(f"config file for watcher #{args.watcher_num} was not found")
        current_clab_config['topology']['nodes'].setdefault(self.ISIS_FILTER_NODE_NAME, dict()).update( copy.deepcopy(self.watcher_config_template_yml['topology']['nodes'][self.ISIS_FILTER_NODE_NAME]) )
        current_clab_config['topology']['nodes'][self.ISIS_FILTER_NODE_NAME]['image'] = self.ISIS_FILTER_NODE_IMAGE
        current_clab_config['topology']['nodes'][self.ISIS_FILTER_NODE_NAME]['network-mode'] = "host"
        current_clab_config['topology']['nodes'][self.ISIS_FILTER_NODE_NAME]['env']['VTAP_HOST_INTERFACE'] = self.host_veth

        current_clab_config['topology']['nodes']['h2'].setdefault('stages', dict()).update( copy.deepcopy(self.watcher_config_template_yml['topology']['nodes']['h2']['stages']) )
        self._do_save_watcher_config_file(current_clab_config)
        print("XDP enabled")

    def disable_xdp(self):
        self.import_from(watcher_num=args.watcher_num)
        current_clab_config = self.watcher_config_file_yml
        if not current_clab_config:
            raise ValueError(f"config file for watcher #{args.watcher_num} was not found")
        del current_clab_config['topology']['nodes'][self.ISIS_FILTER_NODE_NAME]
        for d in current_clab_config['topology']['nodes']['h2']['stages']['create']['wait-for']:
            if d.get("node") == self.ISIS_FILTER_NODE_NAME:
                d['node'] = 'h1'
        self._do_save_watcher_config_file(current_clab_config)
        print("XDP disabled")

if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description="Provisioning Watcher instances for tracking IS-IS topology changes"
    )
    parser.add_argument(
        "--action", required=True, help="Options: add_watcher, enable_xdp, disable_xdp, diagnostic"
    )
    parser.add_argument(
        "--watcher_num", required=False, default=0, type=int, help="Number of watcher"
    )
    
    args = parser.parse_args()
    allowed_actions = [actions.value for actions in ACTIONS]
    if args.action not in allowed_actions:
        raise ValueError(f"Not allowed action. Supported actions: {', '.join(allowed_actions)}")
    try:
        watcher_conf = WATCHER_CONFIG.parse_command_args(args)
    except KeyboardInterrupt:
        print("\nInterrupted. Bye!")
        sys.exit(1)