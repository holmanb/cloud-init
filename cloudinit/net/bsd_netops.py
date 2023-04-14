from typing import Optional


def link_up(interface: str):
    pass


def link_down(interface: str):
    pass


def add_route(
    interface: str,
    route: str,
    *,
    gateway: Optional[str] = None,
    source_address: Optional[str] = None
):
    pass


def append_route(address: str, interface: str, gateway: str):
    pass


def del_route(
    interface: str,
    address: str,
    *,
    gateway: Optional[str] = None,
    source_address: Optional[str] = None
):
    pass


def get_default_route() -> str:
    pass


def add_addr(interface: str, address: str, broadcast: str):
    pass


def del_addr(interface: str, address: str):
    pass
