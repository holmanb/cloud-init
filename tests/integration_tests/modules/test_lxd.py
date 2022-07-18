"""Integration tests for LXD bridge creation.

(This is ported from
``tests/cloud_tests/testcases/modules/lxd_bridge.yaml``.)
"""
import pytest
import yaml

from tests.integration_tests.util import verify_clean_log

BRIDGE_USER_DATA = """\
#cloud-config
lxd:
  init:
    storage_backend: btrfs
  bridge:
    mode: new
    name: lxdbr0
    ipv4_address: 10.100.100.1
    ipv4_netmask: 24
    ipv4_dhcp_first: 10.100.100.100
    ipv4_dhcp_last: 10.100.100.200
    ipv4_nat: true
    domain: lxd
    mtu: 9000
"""

STORAGE_USER_DATA = """\
#cloud-config
bootcmd: [ "apt-get --yes remove {}" ]
lxd:
  init:
    storage_backend: {}
"""


@pytest.mark.no_container
@pytest.mark.user_data(BRIDGE_USER_DATA)
class TestLxdBridge:
    @pytest.mark.parametrize("binary_name", ["lxc", "lxd"])
    def test_binaries_installed(self, class_client, binary_name):
        """Check that the expected LXD binaries are installed"""
        assert class_client.execute(["which", binary_name]).ok

    def test_bridge(self, class_client):
        """Check that the given bridge is configured"""
        cloud_init_log = class_client.read_from_file("/var/log/cloud-init.log")
        verify_clean_log(cloud_init_log)

        # The bridge should exist
        assert class_client.execute("ip addr show lxdbr0")

        raw_network_config = class_client.execute("lxc network show lxdbr0")
        network_config = yaml.safe_load(raw_network_config)
        assert "10.100.100.1/24" == network_config["config"]["ipv4.address"]


def validate_storage(validate_client):
    verify_clean_log(
        validate_client.read_from_file("/var/log/cloud-init.log"),
        ignore_deprecations=False,
    )


@pytest.mark.no_container
@pytest.mark.user_data(STORAGE_USER_DATA.format("btrfs-progs", "btrfs"))
def test_storage_btrfs(client):
    validate_storage(client)


@pytest.mark.no_container
@pytest.mark.user_data(STORAGE_USER_DATA.format("lvm2", "lvm"))
def test_storage_lvm(client):
    validate_storage(client)


@pytest.mark.no_container
@pytest.mark.user_data(STORAGE_USER_DATA.format("zfsutils-linux", "zfs"))
def test_storage_zfs(client):
    validate_storage(client)
