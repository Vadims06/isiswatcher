# Log IS-IS topology (FRR based) changes using IS-IS watcher

This lab consists of 6 FRR routers and a single IS-IS Watcher. Each router is pre-configured for being in IS-IS domain with different network type. Topology changes are printed in a text file only (which is enough for testing), for getting logs exported to ELK or Topolograph (to see network changes on a map) start `docker-compose` files and follow instructions on main README.

## Network schema
![IS-IS watcher containerlab](container_lab.drawio.png)
To emulate broadcast network a `br-dr` linux bridge is needed to be preconfigured. Use these commands to create the bridge:
```
sudo brctl addbr br-dr
sudo ip link set up dev br-dr
```

### Start
```
sudo clab deploy --topo frr01.clab.yml
```

### Connection to a router
```
sudo docker exec -it clab-frr01-router2 vtysh
```

### IS-IS Watcher logs
Available under `watcher` folder. To see them:
```
sudo tail -f watcher/watcher.log
```

### Actions
1. Start watching IS-IS watcher logs
2. Connect to a router and apply any changes: interface up/down, is-is metric cost change
3. Track your changes in the watcher logs.

Note:
log file should have `systemd-network:systemd-journal` ownership

> **Note**  
> This lab is based on simple FRR for building topology based on frr routers, more information about it is available here: https://www.brianlinkletter.com/2021/05/use-containerlab-to-emulate-open-source-routers/

