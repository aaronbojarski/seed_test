#!/usr/bin/env python3

import time

import docker
import python_on_whales
import yaml

from ipaddress import IPv4Network
from seedemu.compiler import Docker
from seedemu.core import Emulator, Binding, Filter
from seedemu.layers import ScionBase, ScionRouting, ScionIsd, Scion, Ospf
from seedemu.layers.Scion import LinkType as ScLinkType
from seedemu.services import ScionBwtestService

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
base = ScionBase()
routing = ScionRouting()
ospf = Ospf()
scion_isd = ScionIsd()
scion = Scion()
bwtest = ScionBwtestService()


with open("config/config.yaml", "r") as f:
    config = yaml.safe_load(f)

routers = {}
core_ases = {}
for _isd in range(1, config["MAIN"]["ISDs"] + 1):
    isd_config = config[f"ISD{_isd}"]
    isd = isd_config["ISDN"]
    isd_ix = 100 + isd
    base.createInternetExchange(isd_ix, create_rs=False)
    routers[isd] = {}
    core_ases[isd] = []
    base.createIsolationDomain(isd)
    for _, as_data in isd_config["ASes"]["CORE"].items():
        asn = as_data["ASN"]
        routers[isd][asn] = {}
        core_as = base.createAutonomousSystem(asn)
        scion_isd.addIsdAs(isd, asn, is_core=True)
        for br_id in range(as_data["BRs"]):
            core_as.createNetwork(f'net{br_id}')
            boarder_router = core_as.createRouter(f'br{br_id}')
            routers[isd][asn][br_id] = boarder_router
            boarder_router.joinNetwork(f'net{br_id}')
            boarder_router.joinNetwork(f'net{(br_id + 1) % as_data["BRs"]}')
            if f"br{br_id}" == as_data["INTER_BR"]:
                boarder_router.joinNetwork(f"ix{isd_ix}")
        core_as.createControlService('cs1').joinNetwork('net0')
        core_as.createControlService('cs2').joinNetwork('net1')
        for previous_core_as in core_ases[isd]:
            print(isd_ix, (isd, asn), (isd, previous_core_as))
            scion.addIxLink(isd_ix, (isd, previous_core_as), (isd, asn), ScLinkType.Core, a_router="br1", b_router="br1")
        core_ases[isd] += [asn]
        print("CORE", core_ases)

    for level in range(1, isd_config["LEVELS"] + 1):
        for _, as_data in isd_config["ASes"][f"LEVEL{level}"].items():
            asn = as_data["ASN"]
            routers[isd][asn] = {}
            customer_as = base.createAutonomousSystem(asn)
            scion_isd.addIsdAs(isd, asn, is_core=False)
            scion_isd.setCertIssuer((isd, asn), core_ases[isd][0])
            customer_as.createNetwork(f'net0')
            boarder_router = customer_as.createRouter(f'br0')
            routers[isd][asn][0] = boarder_router
            boarder_router.joinNetwork(f'net0')
            customer_as.createControlService('cs1').joinNetwork('net0')
            if "HOST" in as_data:
                customer_as.createHost('host').joinNetwork('net0', address=f'10.{asn}.0.30')
                host = customer_as.getHost('host')
                host.addSoftware("git")
                # install go 1.25.1
                host.addBuildCommand("rm -rf /usr/local/go && curl -LO https://golang.org/dl/go1.25.1.linux-amd64.tar.gz && \
                    echo \"7716a0d940a0f6ae8e1f3b3f4f36299dc53e31b16840dbd171254312c41ca12e go1.25.1.linux-amd64.tar.gz\" | sha256sum -c && \
                    tar -C /usr/local -xzf go1.25.1.linux-amd64.tar.gz \
                    && rm go1.25.1.linux-amd64.tar.gz")
                # install scion-fast-failover
                host.addBuildCommand("git clone https://github.com/aaronbojarski/scion-fast-failover.git && \
                    cd scion-fast-failover && \
                    /usr/local/go/bin/go build fast-failover.go server.go client.go")

            for connection in as_data["CONNECTIONS"]:
                print(connection)
                boarder_router.crossConnect(connection["AS"], connection["BR"], xc_nets.next_addr(f"{connection["AS"]}-{asn}"))
                routers[isd][connection["AS"]][0].crossConnect(asn, "br0", xc_nets.next_addr(f"{connection["AS"]}-{asn}"))
                if connection["RELATION"] == "PROVIDER":
                    scion.addXcLink((isd, connection["AS"]), (isd, asn), ScLinkType.Transit)
                elif connection["RELATION"] == "PEER":
                    scion.addXcLink((isd, connection["AS"]), (isd, asn), ScLinkType.Peer)

# Rendering
emu.addLayer(base)
emu.addLayer(routing)
emu.addLayer(ospf)
emu.addLayer(scion_isd)
emu.addLayer(scion)
emu.addLayer(bwtest)

emu.render()

# Compilation
emu.compile(Docker(internetMapPort=5000), './output', override=True)
