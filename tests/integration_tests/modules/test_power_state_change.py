"""Integration test of the cc_power_state_change module.

Test that the power state config options work as expected.
"""

import time

import pytest

from tests.integration_tests.instances import IntegrationInstance
from tests.integration_tests.releases import IS_UBUNTU
from tests.integration_tests.util import verify_ordered_items_in_text

USER_DATA = """\
#cloud-config
power_state:
  delay: {delay}
  mode: {mode}
  message: msg
  timeout: {timeout}
"""
USER_DATA_CONDITION = USER_DATA + "\n  condition: {condition} "


def _detect_reboot(instance: IntegrationInstance):
    # We'll wait for instance up here, but we don't know if we're
    # detecting the first boot or second boot, so we also check
    # the logs to ensure we've booted twice. If the logs show we've
    # only booted once, wait until we've booted twice
    instance.instance.wait()
    for _ in range(600):
        try:
            log = instance.read_from_file("/var/log/cloud-init.log")
            boot_count = log.count("running 'init-local'")
            if boot_count == 1:
                instance.instance.wait()
            elif boot_count > 1:
                break
        except Exception:
            pass
        time.sleep(1)
    else:
        raise Exception("Could not detect reboot")


def _can_connect(instance):
    return instance.execute("true").ok


# Note: This test led to the discovery of
# https://github.com/canonical/pycloudlib/issues/369
@pytest.mark.skipif(not IS_UBUNTU, reason="Only ever tested on Ubuntu")
class PowerChange:
    def _poweroff(self, instance, expected):
        assert _can_connect(instance)
        verify_ordered_items_in_text(
            [
                "Running module power_state_change",
                expected,
                "running 'init-local'",
                "config-power_state_change already ran",
            ],
            instance.read_from_file("/var/log/cloud-init.log"),
        )

    @pytest.mark.user_data(
        USER_DATA.format(delay="now", mode="poweroff", timeout="10")
    )
    def test_poweroff(self, client: IntegrationInstance):
        client.instance.wait_for_stop()
        client.instance.start(wait=True)
        self._poweroff(
            client,
            "will execute: shutdown -P now msg",
        )

    @pytest.mark.user_data(
        USER_DATA.format(delay="now", mode="reboot", timeout="10")
    )
    def test_reboot(self, client: IntegrationInstance):
        _detect_reboot(client)
        self._poweroff(
            client,
            "will execute: shutdown -r now msg",
        )

    @pytest.mark.user_data(
        USER_DATA.format(delay="+1", mode="halt", timeout="0")
    )
    def test_halt(self, client: IntegrationInstance):
        client.instance.wait_for_stop()
        client.instance.start(wait=True)
        self._poweroff(
            client,
            "will execute: shutdown -H +1 msg",
        )


class TestPowerOffCondition(PowerChange):
    @pytest.mark.user_data(
        USER_DATA_CONDITION.format(
            delay="0", mode="poweroff", timeout="0", condition="false"
        )
    )
    def test_poweroff_false_condition(self, client: IntegrationInstance):
        log = client.read_from_file("/var/log/cloud-init.log")
        assert _can_connect(client)
        assert "Condition was false. Will not perform state change" in log
