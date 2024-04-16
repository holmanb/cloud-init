"""Tests for `cloud-init status`"""
from textwrap import dedent

import pytest

from tests.integration_tests.integration_settings import PLATFORM
from tests.integration_tests.instances import IntegrationInstance
from tests.integration_tests.releases import CURRENT_RELEASE, MANTIC
from tests.integration_tests.util import verify_clean_log


VALID_USER_DATA = """\
#cloud-config
runcmd:
  - echo 'hi' > /var/tmp/test
"""

# The '-' in 'hashed-password' fails schema validation
INVALID_USER_DATA_SCHEMA = """\
#cloud-config
users:
  - default
  - name: newsuper
    gecos: Big Stuff
    groups: users, admin
    sudo: ALL=(ALL) NOPASSWD:ALL
    hashed-password: asdfasdf
    shell: /bin/bash
    lock_passwd: true
"""

INVALID_USER_DATA_HEADER = """\
runcmd:
  - echo 'hi' > /var/tmp/test
"""

USER_DATA = """\
#cloud-config
apt_update: false
apt_upgrade: false
apt_reboot_if_required: false
"""

NET_CFG_V1 = """\
network:
  version: 1
  config:
  - type: physical
    name: eth0
    subnets:
      - type: dhcp
"""
NET_CFG_V1_INVALID = NET_CFG_V1.replace("config", "junk")
NET_V1_ANNOTATED = """\
network:		# E1,E2
  version: 1
  junk:
  - type: physical
    name: eth0
    subnets:
      - type: dhcp

# Errors: -------------
# E1: 'config' is a required property
# E2: Additional properties are not allowed ('junk' was unexpected)"""

NET_CFG_V2 = """\
version: 2
ethernets:
  eth0:
    dhcp4: true
"""
NET_CFG_V2_INVALID = NET_CFG_V2.replace("true", "bogus")
NET_V2_ANNOTATED = """\
---
network:
    ethernets:
        eth0:
            dhcp4: bogus		# E1
    version: 2
...

# Errors: -------------
# E1: Invalid netplan schema. Error in network definition: invalid boolean value 'bogus'"""  # noqa: E501


@pytest.mark.user_data(USER_DATA)
class TestSchemaDeprecations:
    def test_clean_log(self, class_client: IntegrationInstance):
        log = class_client.read_from_file("/var/log/cloud-init.log")
        verify_clean_log(log, ignore_deprecations=True)
        assert "DEPRECATED]: Deprecated cloud-config provided:" in log
        assert "apt_reboot_if_required: Default: ``false``. Deprecated " in log
        assert "apt_update: Default: ``false``. Deprecated in version" in log
        assert "apt_upgrade: Default: ``false``. Deprecated in version" in log

    def test_network_config_schema_validation(
        self, class_client: IntegrationInstance
    ):
        content_responses = {
            NET_CFG_V1: {"out": "Valid schema /root/net.yaml"},
            NET_CFG_V1_INVALID: {
                "out": "Invalid network-config /root/net.yaml",
                "err": (
                    "network: Additional properties are not allowed"
                    " ('junk' was unexpected)"
                ),
                "annotate": NET_V1_ANNOTATED,
            },
        }
        if CURRENT_RELEASE >= MANTIC:
            # Support for netplan API available
            content_responses[NET_CFG_V2] = {
                "out": "Valid schema /root/net.yaml"
            }
            content_responses[NET_CFG_V2_INVALID] = {
                "out": "Invalid network-config /root/net.yaml",
                "err": (
                    "Cloud config schema errors: format-l5.c20:"
                    " Invalid netplan schema. Error in network definition:"
                    " invalid boolean value 'bogus'"
                ),
                "annotate": NET_V2_ANNOTATED,
            }
        else:
            # No netplan API available skips validation
            content_responses[NET_CFG_V2] = {
                "out": (
                    "Skipping network-config schema validation."
                    " No network schema for version: 2"
                )
            }
            content_responses[NET_CFG_V2_INVALID] = {
                "out": (
                    "Skipping network-config schema validation."
                    " No network schema for version: 2"
                )
            }

        for content, responses in content_responses.items():
            class_client.write_to_file("/root/net.yaml", content)
            result = class_client.execute(
                "cloud-init schema --schema-type network-config"
                " --config-file /root/net.yaml"
            )
            assert responses["out"] == result.stdout
            if responses.get("err"):
                assert responses["err"] in result.stderr
            if responses.get("annotate"):
                result = class_client.execute(
                    "cloud-init schema --schema-type network-config"
                    " --config-file /root/net.yaml --annotate"
                )
                assert responses["annotate"] in result.stdout

    def test_schema_deprecations(self, class_client: IntegrationInstance):
        """Test schema behavior with deprecated configs."""
        user_data_fn = "/root/user-data"
        class_client.write_to_file(user_data_fn, USER_DATA)

        result = class_client.execute(
            f"cloud-init schema --config-file {user_data_fn}"
        )
        assert (
            result.ok
        ), "`schema` cmd must return 0 even with deprecated configs"
        assert not result.stderr
        assert "Cloud config schema deprecations:" in result.stdout
        assert (
            "apt_update: Default: ``false``. Deprecated in version"
            in result.stdout
        )
        assert (
            "apt_upgrade: Default: ``false``. Deprecated in version"
            in result.stdout
        )
        assert (
            "apt_reboot_if_required: Default: ``false``. Deprecated in version"
            in result.stdout
        )

        annotated_result = class_client.execute(
            f"cloud-init schema --annotate --config-file {user_data_fn}"
        )
        assert (
            annotated_result.ok
        ), "`schema` cmd must return 0 even with deprecated configs"
        assert not annotated_result.stderr
        expected_output = dedent(
            """\
            #cloud-config
            apt_update: false\t\t# D1
            apt_upgrade: false\t\t# D2
            apt_reboot_if_required: false\t\t# D3

            # Deprecations: -------------
            # D1: Default: ``false``. Deprecated in version 22.2. Use ``package_update`` instead.
            # D2: Default: ``false``. Deprecated in version 22.2. Use ``package_upgrade`` instead.
            # D3: Default: ``false``. Deprecated in version 22.2. Use ``package_reboot_if_required`` instead.


            Valid schema /root/user-data"""  # noqa: E501
        )
        assert expected_output in annotated_result.stdout


@pytest.mark.user_data(VALID_USER_DATA)
class TestValidUserData:
    def test_schema_status(self, class_client: IntegrationInstance):
        """Test `cloud-init schema` with valid userdata.

        PR #575
        """
        result = class_client.execute("cloud-init schema --system")
        assert result.ok
        assert "Valid schema user-data" in result.stdout.strip()
        result = class_client.execute("cloud-init status --long")
        assert 0 == result.return_code, (
            f"Unexpected exit {result.return_code} from cloud-init status:"
            f" {result}"
        )

    def test_modules_init(self, class_client: IntegrationInstance):
        for mode in ("init", "config", "final"):
            result = class_client.execute(f"cloud-init modules --mode {mode}")
            assert result.ok
            assert f"'modules:{mode}'" in result.stdout.strip()


@pytest.mark.skipif(
    PLATFORM == "qemu", reason="QEMU only supports #cloud-config userdata"
)
@pytest.mark.user_data(INVALID_USER_DATA_HEADER)
def test_invalid_userdata(client: IntegrationInstance):
    """Test `cloud-init schema` with invalid userdata.

    PR #575
    """
    result = client.execute("cloud-init schema --system")
    assert not result.ok
    assert "Cloud config schema errors" in result.stderr
    assert (
        "Expected first line to be one of: #!, ## template: jinja,"
        " #cloud-boothook, #cloud-config" in result.stderr
    )
    result = client.execute("cloud-init status --long")
    if CURRENT_RELEASE.series in ("focal", "jammy", "lunar", "mantic"):
        return_code = 0  # Stable releases don't change exit code behavior
    else:
        return_code = 2  # 23.4 and later will exit 2 on warnings
    assert (
        return_code == result.return_code
    ), f"Unexpected exit code {result.return_code}"
