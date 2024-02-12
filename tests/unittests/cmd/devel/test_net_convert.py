# This file is part of cloud-init. See LICENSE file for license information.

import itertools

import pytest

from cloudinit import safeyaml as yaml
from cloudinit.cmd.devel import net_convert
from cloudinit.distros.debian import NETWORK_FILE_HEADER
from tests.unittests.helpers import mock

M_PATH = "cloudinit.cmd.devel.net_convert."


required_args = [
    "--directory",
    "--network-data",
    "--distro=ubuntu",
    "--kind=eni",
    "--output-kind=eni",
]


SAMPLE_NET_V1 = """\
network:
  version: 1
  config:
  - type: physical
    name: eth0
    subnets:
      - type: dhcp
"""


SAMPLE_NETPLAN_CONTENT = f"""\
{NETWORK_FILE_HEADER}network:
    version: 2
    ethernets:
        eth0:
            dhcp4: true
"""

SAMPLE_ENI_CONTENT = f"""\
{NETWORK_FILE_HEADER}auto lo
iface lo inet loopback

auto eth0
iface eth0 inet dhcp
"""

SAMPLE_NETWORKD_CONTENT = """\
[Match]
Name=eth0

[Network]
DHCP=ipv4

"""

SAMPLE_SYSCONFIG_CONTENT = """\
# Created by cloud-init automatically, do not edit.
#
BOOTPROTO=dhcp
DEVICE=eth0
NM_CONTROLLED=no
ONBOOT=yes
TYPE=Ethernet
USERCTL=no
"""

SAMPLE_NETWORK_MANAGER_CONTENT = """\
# Generated by cloud-init. Changes will be lost.

[connection]
id=cloud-init eth0
uuid=1dd9a779-d327-56e1-8454-c65e2556c12c
autoconnect-priority=120
type=ethernet
interface-name=eth0

[user]
org.freedesktop.NetworkManager.origin=cloud-init

[ethernet]

[ipv4]
method=auto
may-fail=false

"""


class TestNetConvert:

    missing_required_args = itertools.combinations(
        required_args, len(required_args) - 1
    )

    def _replace_path_args(self, cmd, tmpdir):
        """Inject tmpdir replacements for parameterize args."""
        updated_cmd = []
        for arg in cmd:
            if arg == "--network-data":
                net_file = tmpdir.join("net")
                net_file.write("")
                updated_cmd.append(f"--network-data={net_file}")
            elif arg == "--directory":
                updated_cmd.append(f"--directory={tmpdir.strpath}")
            else:
                updated_cmd.append(arg)
        return updated_cmd

    @pytest.mark.parametrize("cmdargs", missing_required_args)
    def test_argparse_error_on_missing_args(self, cmdargs, capsys, tmpdir):
        """Log the appropriate error when required args are missing."""
        params = self._replace_path_args(cmdargs, tmpdir)
        with mock.patch("sys.argv", ["net-convert"] + params):
            with pytest.raises(SystemExit):
                net_convert.get_parser().parse_args()
        _out, err = capsys.readouterr()
        assert "the following arguments are required" in err

    @pytest.mark.parametrize("debug", (False, True))
    @pytest.mark.parametrize(
        "output_kind,outfile_content",
        (
            (
                "netplan",
                {"etc/netplan/50-cloud-init.yaml": SAMPLE_NETPLAN_CONTENT},
            ),
            (
                "eni",
                {
                    "etc/network/interfaces.d/50-cloud-init.cfg": SAMPLE_ENI_CONTENT  # noqa: E501
                },
            ),
            (
                "networkd",
                {
                    "etc/systemd/network/10-cloud-init-eth0.network": SAMPLE_NETWORKD_CONTENT  # noqa: E501
                },
            ),
            (
                "sysconfig",
                {
                    "etc/sysconfig/network-scripts/ifcfg-eth0": SAMPLE_SYSCONFIG_CONTENT  # noqa: E501
                },
            ),
            (
                "network-manager",
                {
                    "etc/NetworkManager/system-connections/cloud-init-eth0.nmconnection": SAMPLE_NETWORK_MANAGER_CONTENT  # noqa: E501
                },
            ),
        ),
    )
    def test_convert_output_kind_artifacts(
        self, output_kind, outfile_content, debug, capsys, tmpdir
    ):
        """Assert proper output-kind artifacts are written."""
        network_data = tmpdir.join("network_data")
        network_data.write(SAMPLE_NET_V1)
        distro = "centos" if output_kind == "sysconfig" else "ubuntu"
        args = [
            f"--directory={tmpdir.strpath}",
            f"--network-data={network_data.strpath}",
            f"--distro={distro}",
            "--kind=yaml",
            f"--output-kind={output_kind}",
        ]
        if debug:
            args.append("--debug")
        params = self._replace_path_args(args, tmpdir)
        with mock.patch("sys.argv", ["net-convert"] + params):
            args = net_convert.get_parser().parse_args()
        with mock.patch("cloudinit.util.chownbyname") as chown:
            net_convert.handle_args("somename", args)
        for path in outfile_content:
            outfile = tmpdir.join(path)
            assert outfile_content[path] == outfile.read()
            if output_kind == "networkd":
                assert [
                    mock.call(
                        outfile.strpath, "systemd-network", "systemd-network"
                    )
                ] == chown.call_args_list

    @pytest.mark.parametrize("debug", (False, True))
    def test_convert_netplan_passthrough(self, debug, tmpdir):
        """Assert that if the network config's version is 2 and the renderer is
        Netplan, then the config is passed through as-is.
        """
        network_data = tmpdir.join("network_data")
        # `default` as a route supported by Netplan but not by cloud-init
        content = """\
        network:
          version: 2
          ethernets:
            enp0s3:
              dhcp4: false
              addresses: [10.0.4.10/24]
              nameservers:
                addresses: [10.0.4.1]
              routes:
              - to: default
                via: 10.0.4.1
                metric: 100
        """
        network_data.write(content)
        args = [
            "-m",
            "enp0s3,AA",
            f"--directory={tmpdir.strpath}",
            f"--network-data={network_data.strpath}",
            "--distro=ubuntu",
            "--kind=yaml",
            "--output-kind=netplan",
        ]
        if debug:
            args.append("--debug")
        params = self._replace_path_args(args, tmpdir)
        with mock.patch("sys.argv", ["net-convert"] + params):
            args = net_convert.get_parser().parse_args()
        with mock.patch("cloudinit.util.chownbyname"):
            net_convert.handle_args("somename", args)
        outfile = tmpdir.join("etc/netplan/50-cloud-init.yaml")
        assert yaml.load(content) == yaml.load(outfile.read())