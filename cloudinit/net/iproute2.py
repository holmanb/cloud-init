from typing import Optional

from cloudinit import subp


def link_up(interface: str, family: Optional[str] = None):
    subp.subp(
        ["ip"] + (
            ["-family", family] if family else []
        ) + ["link", "set", "dev", interface, "up"]
    )


def link_down(interface: str, family: Optional[str] = None):
    subp.subp(
        ["ip"] + (
            ["-family", family] if family else []
        ) + ["link", "set", "dev", interface, "down"]
    )


def add_route(
    interface: str,
    route: str,
    *,
    gateway: Optional[str] = None,
    source_address: Optional[str] = None,
):
    subp.subp(
        ["ip", "-4", "route", "add", route]
        + (["via", gateway] if gateway and gateway != "0.0.0.0" else [])
        + [
            "dev",
            interface,
        ]
        + (["src", source_address] if source_address else []),
    )


def append_route(interface: str, address: str, gateway: str):
    subp.subp(
        ["ip", "-4", "route", "append", address]
        + (["via", gateway] if gateway and gateway != "0.0.0.0" else [])
        + ["dev", interface]
    )


def del_route(
    interface: str,
    address: str,
    *,
    gateway: Optional[str] = None,
    source_address: Optional[str] = None,
):
    subp.subp(
        ["ip", "-4", "route", "del", address]
        + (["via", gateway] if gateway and gateway != "0.0.0.0" else [])
        + ["dev", interface]
        + (["src", source_address] if source_address else [])
    )


def get_default_route() -> str:
    return subp.subp(
        ["ip", "route", "show", "0.0.0.0/0"],
    ).stdout


def add_addr(interface: str, address: str, broadcast: str):
    subp.subp(
        [
            "ip",
            "-family",
            "inet",
            "addr",
            "add",
            address,
            "broadcast",
            broadcast,
            "dev",
            interface,
        ],
        update_env={"LANG": "C"},
    )


def del_addr(interface: str, address: str):
    subp.subp(
        ["ip", "-family", "inet", "addr", "del", address, "dev", interface]
    )
