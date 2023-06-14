from typing import Optional


def link_up(interface: str):
    raise FileNotFoundError("test")


def link_down(interface: str):
    raise FileNotFoundError("test")


def add_route(
    interface: str,
    route: str,
    *,
    gateway: Optional[str] = None,
    source_address: Optional[str] = None
):
    raise FileNotFoundError("test")


def append_route(address: str, interface: str, gateway: str):
    raise FileNotFoundError("test")


def del_route(
    interface: str,
    address: str,
    *,
    gateway: Optional[str] = None,
    source_address: Optional[str] = None
):
    raise FileNotFoundError("test")


def get_default_route() -> str:
    raise FileNotFoundError("test")


def add_addr(interface: str, address: str, broadcast: str):
    raise FileNotFoundError("test")


def del_addr(interface: str, address: str):
    raise FileNotFoundError("test")
