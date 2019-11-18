#!/bin/bash
#
# Called before running pyspawner tests to set up the outer environment.
#
# Requires:
#
# * NET_ADMIN: to create iptables rules

set -e

# NetworkConfig mimics pyspawner/sandbox.py
KERNEL_VETH=veth-pyspawn
CHILD_VETH_IP4="192.168.123.2"

# iptables
# "ip route get 1.1.1.1" will display the default route. It looks like:
#     1.1.1.1 via 192.168.86.1 dev wlp2s0 src 192.168.86.70 uid 1000
# Grep for the "src x.x.x.x" part and store the "x.x.x.x"
ipv4_snat_source=$(ip route get 1.1.1.1 | grep -oe "src [^ ]\+" | cut -d' ' -f2)
cat << EOF | iptables-legacy-restore --noflush
*filter
:INPUT ACCEPT
:FORWARD DROP
# Block access to the host itself from a module.
-A INPUT -i $KERNEL_VETH -j REJECT
# Allow forwarding response packets back to our module (even
# though our module's IP is in UNSAFE_IPV4_ADDRESS_BLOCKS).
-A FORWARD -o $KERNEL_VETH -j ACCEPT
# Block unsafe destination addresses. Modules should not be
# able to access internal services. (Not even our DNS server.)
-A FORWARD -d 0.0.0.0/8          -i $KERNEL_VETH -j REJECT
-A FORWARD -d 10.0.0.0/8         -i $KERNEL_VETH -j REJECT
-A FORWARD -d 100.64.0.0/10      -i $KERNEL_VETH -j REJECT
-A FORWARD -d 127.0.0.0/8        -i $KERNEL_VETH -j REJECT
-A FORWARD -d 169.254.0.0/16     -i $KERNEL_VETH -j REJECT
-A FORWARD -d 172.16.0.0/12      -i $KERNEL_VETH -j REJECT
-A FORWARD -d 192.0.0.0/24       -i $KERNEL_VETH -j REJECT
-A FORWARD -d 192.0.2.0/24       -i $KERNEL_VETH -j REJECT
-A FORWARD -d 192.88.99.0/24     -i $KERNEL_VETH -j REJECT
-A FORWARD -d 192.168.0.0/16     -i $KERNEL_VETH -j REJECT
-A FORWARD -d 198.18.0.0/15      -i $KERNEL_VETH -j REJECT
-A FORWARD -d 198.51.100.0/24    -i $KERNEL_VETH -j REJECT
-A FORWARD -d 203.0.113.0/24     -i $KERNEL_VETH -j REJECT
-A FORWARD -d 224.0.0.0/4        -i $KERNEL_VETH -j REJECT
-A FORWARD -d 240.0.0.0/4        -i $KERNEL_VETH -j REJECT
-A FORWARD -d 255.255.255.255/32 -i $KERNEL_VETH -j REJECT
# Allow forwarding exactly the source address of the module.
# Don't forward just any address (i.e. don't set policy
# ACCEPT): if a module somehow gains CAP_NET_ADMIN (which
# shouldn't happen) it should not be able to spoof source
# addresses.
-A FORWARD -i $KERNEL_VETH -s $CHILD_VETH_IP4 -j ACCEPT
COMMIT
*nat
:POSTROUTING ACCEPT
-A POSTROUTING -s $CHILD_VETH_IP4 -j SNAT --to-source $ipv4_snat_source
COMMIT
EOF
