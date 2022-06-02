import ipaddress
import random
import time
import sys
import typing
import queue
import threading
import socket
import struct
# https://datatracker.ietf.org/doc/html/rfc3927#section-2.4

# Assumes L2 supports arp
PROBE_WAIT          =  1 # second   (initial random delay)
PROBE_NUM           =  3 #          (number of probe packets)
PROBE_MIN           =  1 # second   (minimum delay till repeated probe)
PROBE_MAX           =  2 # seconds  (maximum delay till repeated probe)
ANNOUNCE_WAIT       =  2 # seconds  (delay before announcing)
ANNOUNCE_NUM        =  2 #          (number of announcement packets)
ANNOUNCE_INTERVAL   =  2 # seconds  (time between announcement packets)
MAX_CONFLICTS       = 10 #          (max conflicts before rate limiting)
RATE_LIMIT_INTERVAL = 60 # seconds  (delay between successive attempts)
DEFEND_INTERVAL     = 10 # seconds  (minimum interval between defensive ARPs).

ETH_BROADCAST = 'ff:ff:ff:ff:ff:ff'
ETH_TYPE_ARP = 0x0806


# Note: section 1.6 says not to use dhcp in 169.254/16, but cloud-init does


def get_pseudo_random_ip(
        mac) -> typing.Iterator[ipaddress.IPv4Address]:
    """169.254.1.0 to 169.254.254.255"""

    # mac might be identical from copied images, time might be identical from
    # simultaneous launch, hopefully /dev/random is smarter than we are
    # TODO: improve this
    random.seed(
            "".join([
                    mac,
                    str(time.time()),
                    str((f := open("/dev/random", "rb")).read(32))
                ])
    )
    f.close()

    while True:
        yield ipaddress.IPv4Address("169.254.{}.{}".format(
                random.randint(1, 254),
                random.randint(0, 255),
            )
        )

def get_ips(_) -> typing.Iterator[ipaddress.IPv4Address]:
    for i in range(1, 255):
        for j in range(0, 256):
            yield ipaddress.IPv4Address("169.254.{}.{}".format(i, j))


# https://github.com/secdev/scapy/blob/master/scapy/layers/l2.py
# https://jrydberg-blog.tumblr.com/post/10518729490/sending-a-gratuitous-arp-with-python
# https://datatracker.ietf.org/doc/html/rfc3927#section-2.1
def gratuitous_arp(ip, mac, socket):
    sender_hardware_address = mac
    sender_ip_address = 0
    target_hardware_address = 0
    # TODO: continue here
    raise NotImplemented()

    # Broadcast frame in network byte order
    struct.iter_unpack(
        "!h",
        [
            0x0001,  # Ethernet
            0x0800,  # IPv4
            0x0604,  # mac address is 6 bytes, IPv4 address is 4
            0x0002,  # operation:  1 is request, 2 is reply
            hex(mac[0:2]),
            hex(mac[2:4]),
            hex(mac[4:6]),
        ])
    gratuitous_arp = [
        # HTYPE
        struct.pack("!h", 1),
        # PTYPE (IPv4)
        struct.pack("!h", 0x0800),
        # HLEN
        struct.pack("!B", 6),
        # PLEN
        struct.pack("!B", 4),
        # OPER (reply)
        struct.pack("!h", 2),
        # SHA
        ether_addr,
        # SPA
        socket.inet_aton(address),
        # THA
        ether_addr,
        # TPA
        socket.inet_aton(address)
        ]
    ether_frame = [
        # Destination address:
        ether_aton(ETH_BROADCAST),
        # Source address:
        mac,
        # Protocol
        struct.pack("!h", ETH_TYPE_ARP),
        # Data
        ''.join(gratuitous_arp)
        ]
    socket.send(''.join(ether_frame))
    socket.close()

def arp_listen():
    """Needs to timeout every second or so for event checking & cleanup"""
    raise NotImplemented

def listening_thread(queue, event, mac, socket):
    while not event.is_set():
        if val := arp_listen():
            queue.put(val)

def gather_arps(mac, socket):
    """Start thread that listens for arps, queue them"""
    q = queue.Queue()
    e = threading.Event()
    t = threading.Thread(
        name="arp listener",
        target=listening_thread,
        args=(q, e, mac, socket),
    )
    t.start()
    return (q, e, t)

def arp_matches_mac(mac, ret):
    raise NotImplemented


def is_ip_in_use(ip, mac, queue, socket) -> bool:
    t_init = time.time()
    t_max = t_init + PROBE_WAIT
    def time_left():
        return t if (t := t_max - time.time()) >= 0 else 0

    gratuitous_arp(ip, mac, socket)
    while True:
        try:
            val = queue.get(block=True, timeout=time_left)
            if arp_matches_mac(mac, val):
                print("arp matches!")
                print(val)
                return True
            else:
                print("received arp:")
                print(val)
        except queue.Empty:
            break
    return False
    print("Timeout out")

def select_link_local_ipv4_address(mac, socket, scan=False):
    ip = None
    get_ip = get_pseudo_random_ip if not scan else get_ips
    queue, event, thread = gather_arps(mac, socket)
    for ip in get_ip(mac):
        # Use first free address
        if response := is_ip_in_use(ip, mac, queue, socket):
            print(f"Address {ip} is in use: {response}")
        else:
            print(f"Address selected: {ip}")
            if not scan:
                return ip
    print("No address available")
    event.set()
    thread.join()

def main():
    try:
        s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW)
        s.bind((sys.argv[1], ETH_TYPE_ARP))
        mac = s.getsockname()[4]
    except OSError as e:
        print(e)
        raise
    select_link_local_ipv4_address(mac, s, scan=False)

if "__main__" == __name__:
    main()
