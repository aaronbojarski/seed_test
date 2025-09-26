#!/usr/bin/env python3

from seedemu.compiler import Docker
from seedemu.core import Emulator
from seedemu.layers import ScionBase, ScionRouting, ScionIsd, Scion
from seedemu.layers.Scion import LinkType as ScLinkType

# Initialize
emu = Emulator()
base = ScionBase()
routing = ScionRouting()
scion_isd = ScionIsd()
scion = Scion()

# SCION ISDs
base.createIsolationDomain(1)

# Internet Exchange
base.createInternetExchange(100, create_rs=False)

# AS-150
as150 = base.createAutonomousSystem(150)
scion_isd.addIsdAs(1, 150, is_core=True)
as150.createNetwork('net0')
as150.createControlService('cs1').joinNetwork('net0')
as150_router = as150.createRouter('br0')
as150_router.joinNetwork('net0').joinNetwork('ix100')
as150_router.crossConnect(153, 'br0', '10.50.0.2/29')
as150.createHost('time').joinNetwork('net0', address='10.150.0.30')
host = as150.getHost('time')
host.addSoftware("git")
# install go 1.25.1
# install go 1.25.1
host.addBuildCommand("rm -rf /usr/local/go && curl -LO https://golang.org/dl/go1.25.1.linux-amd64.tar.gz && \
    echo \"7716a0d940a0f6ae8e1f3b3f4f36299dc53e31b16840dbd171254312c41ca12e go1.25.1.linux-amd64.tar.gz\" | sha256sum -c && \
    tar -C /usr/local -xzf go1.25.1.linux-amd64.tar.gz \
    && rm go1.25.1.linux-amd64.tar.gz")
# install scion-time
host.addBuildCommand("git clone https://github.com/marcfrei/scion-time.git && \
    cd scion-time && \
    /usr/local/go/bin/go build timeservice.go timeservice_t.go")

ts_config = """
local_address = "1-150,10.150.0.30"
scion_daemon_address = "127.0.0.1:30255"

ntske_cert_file = "/tls.crt"
ntske_key_file = "/tls.key"
ntske_server_name = "localhost"
"""
host.setFile(path="ts_config.toml", content=ts_config)
# kill the dispatcher since it is incompatible with the scion-time server
host.appendStartCommand("pkill dispatcher", isPostConfigCommand=True)
# start the scion-time server
host.appendStartCommand("./scion-time/timeservice server -verbose -config ts_config.toml > time.log 2>&1", fork=True, isPostConfigCommand=True)

# AS-151
as151 = base.createAutonomousSystem(151)
scion_isd.addIsdAs(1, 151, is_core=True)
as151.createNetwork('net0')
as151.createControlService('cs1').joinNetwork('net0')
as151.createRouter('br0').joinNetwork('net0').joinNetwork('ix100')

# AS-152
as152 = base.createAutonomousSystem(152)
scion_isd.addIsdAs(1, 152, is_core=True)
as152.createNetwork('net0')
as152.createControlService('cs1').joinNetwork('net0')
as152.createRouter('br0').joinNetwork('net0').joinNetwork('ix100')

# AS-153
as153 = base.createAutonomousSystem(153)
scion_isd.addIsdAs(1, 153, is_core=False)
scion_isd.setCertIssuer((1, 153), issuer=150)
as153.createNetwork('net0')
as153.createControlService('cs1').joinNetwork('net0')
as153_router = as153.createRouter('br0')
as153_router.joinNetwork('net0')
as153_router.crossConnect(150, 'br0', '10.50.0.3/29')
as153.createHost('time').joinNetwork('net0', address='10.153.0.30')
host = as153.getHost('time')
host.addSoftware("git")
# install go 1.25.1
host.addBuildCommand("rm -rf /usr/local/go && curl -LO https://golang.org/dl/go1.25.1.linux-amd64.tar.gz && \
    echo \"7716a0d940a0f6ae8e1f3b3f4f36299dc53e31b16840dbd171254312c41ca12e go1.25.1.linux-amd64.tar.gz\" | sha256sum -c && \
    tar -C /usr/local -xzf go1.25.1.linux-amd64.tar.gz \
    && rm go1.25.1.linux-amd64.tar.gz")
# install scion-time
host.addBuildCommand("git clone https://github.com/marcfrei/scion-time.git && \
    cd scion-time && \
    /usr/local/go/bin/go build timeservice.go timeservice_t.go")

# Inter-AS routing
scion.addIxLink(100, (1, 150), (1, 151), ScLinkType.Core)
scion.addIxLink(100, (1, 151), (1, 152), ScLinkType.Core)
scion.addIxLink(100, (1, 152), (1, 150), ScLinkType.Core)
scion.addXcLink((1, 150), (1, 153), ScLinkType.Transit)

# Rendering
emu.addLayer(base)
emu.addLayer(routing)
emu.addLayer(scion_isd)
emu.addLayer(scion)

emu.render()

# Compilation
emu.compile(Docker(internetMapPort=5000), './output', override=True)
