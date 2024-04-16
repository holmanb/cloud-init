import pytest

from tests.integration_tests.instances import IntegrationInstance

FAILING_USER_DATA = """\
#cloud-config
bootcmd:
  - exit 1
runcmd:
  - exit 1
"""


@pytest.mark.user_data(FAILING_USER_DATA)
def test_failing_userdata_modules_exit_codes(client: IntegrationInstance):
    """Test failing in modules representd in exit status"""
    for mode in ("init", "config", "final"):
        result = client.execute(f"cloud-init modules --mode {mode}")
        assert result.failed if mode == "init" else result.ok
        assert f"'modules:{mode}'" in result.stdout.strip()
