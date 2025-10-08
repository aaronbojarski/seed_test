#!/usr/bin/env python3

import time

import docker
import python_on_whales
import yaml

from ipaddress import IPv4Network
from seedemu.compiler import Docker
from seedemu.core import Emulator, OptionMode, OptionRegistry
from seedemu.layers import Base, Routing, Ospf, Ibgp, Ebgp, PeerRelationship
from seedemu.layers import PeerRelationship as PeerRel


class CrossConnectNetAssigner:
    def __init__(self):
        self.subnet_iter = IPv4Network("10.3.0.0/16").subnets(new_prefix=29)
        self.xc_nets = {}

    def next_addr(self, net):
        if net not in self.xc_nets:
            hosts = next(self.subnet_iter).hosts()
            next(hosts) # Skip first IP (reserved for Docker)
            self.xc_nets[net] = hosts
        return "{}/29".format(next(self.xc_nets[net]))

xc_nets = CrossConnectNetAssigner()


# Initialize
emu = Emulator()
base = Base()
routing = Routing()
ospf = Ospf()
ibgp = Ibgp()
ebgp = Ebgp()


with open("config/config.yaml", "r") as f:
    config = yaml.safe_load(f)

routers = {}
for _isd in range(1, config["MAIN"]["ISDs"] + 1):
    isd_config = config[f"ISD{_isd}"]
    isd = isd_config["ISDN"]
    isd_ix = 100 + isd
    base.createInternetExchange(isd_ix)
    routers[isd] = {}
    for _, as_data in isd_config["ASes"]["CORE"].items():
        asn = as_data["ASN"]
        routers[isd][asn] = {}
        core_as = base.createAutonomousSystem(asn)
        for br_id in range(as_data["BRs"]):
            core_as.createNetwork(f'net{br_id}')
            boarder_router = core_as.createRouter(f'br{br_id}')
            routers[isd][asn][br_id] = boarder_router
            boarder_router.joinNetwork(f'net{br_id}')
            boarder_router.joinNetwork(f'net{(br_id + 1) % as_data["BRs"]}')
            if f"br{br_id}" == as_data["INTER_BR"]:
                boarder_router.joinNetwork(f"ix{isd_ix}")
        ebgp.addRsPeer(isd_ix, asn)

    for level in range(1, isd_config["LEVELS"] + 1):
        for _, as_data in isd_config["ASes"][f"LEVEL{level}"].items():
            asn = as_data["ASN"]
            routers[isd][asn] = {}
            customer_as = base.createAutonomousSystem(asn)
            customer_as.setOption(OptionRegistry().scion_disable_bfd("false", mode = OptionMode.RUN_TIME))
            customer_as.createNetwork(f'net0')
            boarder_router = customer_as.createRouter(f'br0')
            routers[isd][asn][0] = boarder_router
            boarder_router.joinNetwork(f'net0')
            if "HOST" in as_data and as_data["HOST"]:
                customer_as.createHost('host').joinNetwork('net0', address=f'10.{asn}.0.30')
            for connection in as_data["CONNECTIONS"]:
                print(connection)
                boarder_router.crossConnect(connection["AS"], connection["BR"], xc_nets.next_addr(f"{connection["AS"]}-{asn}"))
                routers[isd][connection["AS"]][0].crossConnect(asn, "br0", xc_nets.next_addr(f"{connection["AS"]}-{asn}"))
                if connection["RELATION"] == "PROVIDER":
                    ebgp.addCrossConnectPeering(connection["AS"], asn, PeerRelationship.Provider)
                elif connection["RELATION"] == "PEER":
                    ebgp.addCrossConnectPeering(connection["AS"], asn, PeerRelationship.Peer)
                else:
                    raise Exception(f"Unknown Connection Relation: {connection["RELATION"]}")

# Rendering
emu.addLayer(base)
emu.addLayer(routing)
emu.addLayer(ospf)
emu.addLayer(ibgp)
emu.addLayer(ebgp)

emu.render()


# Compilation
emu.compile(Docker(internetMapPort=5000), './output_bgp', override=True)

whales = python_on_whales.DockerClient(compose_files=["./output_bgp/docker-compose.yml"])
whales.compose.build()
whales.compose.up(detach=True)

# Use Docker SDK to interact with the containers
client: docker.DockerClient = docker.from_env()
ctrs = {ctr.name: client.containers.get(ctr.id) for ctr in whales.compose.ps()}

print("Started")

# Shut the network down
#whales.compose.down()

