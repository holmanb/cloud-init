"""Integration test for the ssh_authkey_fingerprints module.

This modules specifies two tests regarding the ``ssh_authkey_fingerprints``
module. The first one verifies that we can disable the module behavior while
the second one verifies if the module is working as expected if enabled.

(This is ported from
``tests/cloud_tests/testcases/modules/ssh_auth_key_fingerprints_disable.yaml``,
``tests/cloud_tests/testcases/modules/ssh_auth_key_fingerprints_enable.yaml``.
)"""
import re
from collections import namedtuple
from io import StringIO
from pathlib import Path

import paramiko
import pytest
from paramiko.ssh_exception import SSHException

from tests.integration_tests.decorators import retry
from tests.integration_tests.instances import IntegrationInstance
from tests.integration_tests.integration_settings import PLATFORM
from tests.integration_tests.releases import CURRENT_RELEASE, IS_UBUNTU

USER_DATA_SSH_AUTHKEY_DISABLE = """\
#cloud-config
no_ssh_fingerprints: true
"""

USER_DATA_SSH_AUTHKEY_ENABLE = """\
#cloud-config
ssh_genkeytypes:
  - ecdsa
  - ed25519
ssh_authorized_keys:
  - ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAACAQDXW9Gg5H7ehjdSc6qDzwNtgCy94XYHhEYlXZMO2+FJrH3wfHGiMfCwOHxcOMt2QiXItULthdeQWS9QjBSSjVRXf6731igFrqPFyS9qBlOQ5D29C4HBXFnQggGVpBNJ82IRJv7szbbe/vpgLBP4kttUza9Dr4e1YM1ln4PRnjfXea6T0m+m1ixNb5432pTXlqYOnNOxSIm1gHgMLxPuDrJvQERDKrSiKSjIdyC9Jd8t2e1tkNLY0stmckVRbhShmcJvlyofHWbc2Ca1mmtP7MlS1VQnfLkvU1IrFwkmaQmaggX6WR6coRJ6XFXdWcq/AI2K6GjSnl1dnnCxE8VCEXBlXgFzad+PMSG4yiL5j8Oo1ZVpkTdgBnw4okGqTYCXyZg6X00As9IBNQfZMFlQXlIo4FiWgj3CO5QHQOyOX6FuEumaU13GnERrSSdp9tCs1Qm3/DG2RSCQBWTfcgMcStIvKqvJ3IjFn0vGLvI3Ampnq9q1SHwmmzAPSdzcMA76HyMUA5VWaBvWHlUxzIM6unxZASnwvuCzpywSEB5J2OF+p6H+cStJwQ32XwmOG8pLp1srlVWpqZI58Du/lzrkPqONphoZx0LDV86w7RUz1ksDzAdcm0tvmNRFMN1a0frDs506oA3aWK0oDk4Nmvk8sXGTYYw3iQSkOvDUUlIsqdaO+w==
"""  # noqa

ASSETS_DIR = Path("tests/integration_tests/assets")
KEY_PATH = ASSETS_DIR / "keys"
KEY_PAIR = namedtuple("key_pair", "public_key private_key")


def get_test_rsa_keypair(key_name: str = "test1") -> KEY_PAIR:
    private_key_path = KEY_PATH / "id_rsa.{}".format(key_name)
    public_key_path = KEY_PATH / "id_rsa.{}.pub".format(key_name)
    with public_key_path.open() as public_file:
        public_key = public_file.read()
    with private_key_path.open() as private_file:
        private_key = private_file.read()
    return KEY_PAIR(public_key, private_key)


TEST_USER1_KEYS = get_test_rsa_keypair("test1")
TEST_USER2_KEYS = get_test_rsa_keypair("test2")
TEST_DEFAULT_KEYS = get_test_rsa_keypair("test3")

_USERDATA = """\
#cloud-config
bootcmd:
 - {bootcmd}
ssh_authorized_keys:
 - {default}
users:
- default
- name: test_user1
  ssh_authorized_keys:
    - {user1}
- name: test_user2
  ssh_authorized_keys:
    - {user2}
""".format(
    bootcmd="{bootcmd}",
    default=TEST_DEFAULT_KEYS.public_key,
    user1=TEST_USER1_KEYS.public_key,
    user2=TEST_USER2_KEYS.public_key,
)


@pytest.mark.ci
class TestSshAuthkeyFingerprints:
    @pytest.mark.user_data(USER_DATA_SSH_AUTHKEY_DISABLE)
    def test_ssh_authkey_fingerprints_disable(self, client):
        cloudinit_output = client.read_from_file("/var/log/cloud-init.log")
        assert (
            "Skipping module named ssh_authkey_fingerprints, "
            "logging of SSH fingerprints disabled" in cloudinit_output
        )

    # retry decorator here because it can take some time to be reflected
    # in syslog
    @retry(tries=30, delay=1)
    @pytest.mark.user_data(USER_DATA_SSH_AUTHKEY_ENABLE)
    def test_ssh_authkey_fingerprints_enable(self, client):
        syslog_output = client.read_from_file("/var/log/syslog")

        assert re.search(r"256 SHA256:.*(ECDSA)", syslog_output) is not None
        assert re.search(r"256 SHA256:.*(ED25519)", syslog_output) is not None
        assert re.search(r"2048 SHA256:.*(RSA)", syslog_output) is None


@pytest.mark.user_data(
    """\
#cloud-config
users:
 - default
 - name: nch
   no_create_home: true
 - name: system
   system: true
"""
)
def test_no_home_directory_created(client: IntegrationInstance):
    """Ensure cc_ssh_authkey_fingerprints doesn't create user directories"""
    home_output = client.execute("ls /home")
    assert "nch" not in home_output
    assert "system" not in home_output

    passwd = client.execute("cat /etc/passwd")
    assert re.search("^nch:", passwd, re.MULTILINE)
    assert re.search("^system:", passwd, re.MULTILINE)


def common_verify(client, expected_keys):
    for user, filename, keys in expected_keys:
        # Ensure key is in the key file
        contents = client.read_from_file(filename)
        if user in ["ubuntu", "root"]:
            lines = contents.split("\n")
            if user == "root":
                # Our personal public key gets added by pycloudlib in
                # addition to the default `ssh_authorized_keys`
                assert len(lines) == 2
            else:
                # Clouds will insert the keys we've added to our accounts
                # or for our launches
                assert len(lines) >= 2
            assert keys.public_key.strip() in contents
        else:
            assert contents.strip() == keys.public_key.strip()

        # Ensure we can actually connect
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        paramiko_key = paramiko.RSAKey.from_private_key(
            StringIO(keys.private_key)
        )

        # Will fail with AuthenticationException if
        # we cannot connect
        ssh.connect(
            client.instance.ip,
            username=user,
            pkey=paramiko_key,
            look_for_keys=False,
            allow_agent=False,
        )

        # Ensure other uses can't connect using our key
        other_users = [u[0] for u in expected_keys if u[2] != keys]
        for other_user in other_users:
            with pytest.raises(SSHException):
                print(
                    "trying to connect as {} with key from {}".format(
                        other_user, user
                    )
                )
                ssh.connect(
                    client.instance.ip,
                    username=other_user,
                    pkey=paramiko_key,
                    look_for_keys=False,
                    allow_agent=False,
                )

        # Ensure we haven't messed with any /home permissions
        # See LP: #1940233
        home_dir = "/home/{}".format(user)
        # Home permissions aren't consistent between releases. On ubuntu
        # this can change to 750 once focal is unsupported.
        if CURRENT_RELEASE.series in ("bionic", "focal"):
            home_perms = "755"
        else:
            home_perms = "750"
        if user == "root":
            home_dir = "/root"
            home_perms = "700"
        assert "{} {}".format(user, home_perms) == client.execute(
            'stat -c "%U %a" {}'.format(home_dir)
        )
        if client.execute("test -d {}/.ssh".format(home_dir)).ok:
            assert "{} 700".format(user) == client.execute(
                'stat -c "%U %a" {}/.ssh'.format(home_dir)
            )
        assert "{} 600".format(user) == client.execute(
            'stat -c "%U %a" {}'.format(filename)
        )

        # Also ensure ssh-keygen works as expected
        client.execute("mkdir {}/.ssh".format(home_dir))
        assert client.execute(
            "ssh-keygen -b 2048 -t rsa -f {}/.ssh/id_rsa -q -N ''".format(
                home_dir
            )
        ).ok
        assert client.execute("test -f {}/.ssh/id_rsa".format(home_dir))
        assert client.execute("test -f {}/.ssh/id_rsa.pub".format(home_dir))

    assert "root 755" == client.execute('stat -c "%U %a" /home')


DEFAULT_KEYS_USERDATA = _USERDATA.format(bootcmd='""')


@pytest.mark.skipif(
    not IS_UBUNTU, reason="Tests permissions specific to Ubuntu releases"
)
@pytest.mark.skipif(
    PLATFORM == "qemu",
    reason="QEMU cloud manually adding key interferes with test",
)
@pytest.mark.user_data(DEFAULT_KEYS_USERDATA)
def test_authorized_keys_default(client: IntegrationInstance):
    expected_keys = [
        (
            "test_user1",
            "/home/test_user1/.ssh/authorized_keys",
            TEST_USER1_KEYS,
        ),
        (
            "test_user2",
            "/home/test_user2/.ssh/authorized_keys",
            TEST_USER2_KEYS,
        ),
        ("ubuntu", "/home/ubuntu/.ssh/authorized_keys", TEST_DEFAULT_KEYS),
        ("root", "/root/.ssh/authorized_keys", TEST_DEFAULT_KEYS),
    ]
    common_verify(client, expected_keys)


AUTHORIZED_KEYS2_USERDATA = _USERDATA.format(
    bootcmd=(
        "sed -i 's;#AuthorizedKeysFile.*;AuthorizedKeysFile "
        "/etc/ssh/authorized_keys %h/.ssh/authorized_keys2;' "
        "/etc/ssh/sshd_config"
    )
)


@pytest.mark.skipif(
    not IS_UBUNTU, reason="Tests permissions specific to Ubuntu releases"
)
@pytest.mark.skipif(
    PLATFORM == "qemu",
    reason="QEMU cloud manually adding key interferes with test",
)
@pytest.mark.user_data(AUTHORIZED_KEYS2_USERDATA)
def test_authorized_keys2(client: IntegrationInstance):
    expected_keys = [
        (
            "test_user1",
            "/home/test_user1/.ssh/authorized_keys2",
            TEST_USER1_KEYS,
        ),
        (
            "test_user2",
            "/home/test_user2/.ssh/authorized_keys2",
            TEST_USER2_KEYS,
        ),
        ("ubuntu", "/home/ubuntu/.ssh/authorized_keys2", TEST_DEFAULT_KEYS),
        ("root", "/root/.ssh/authorized_keys2", TEST_DEFAULT_KEYS),
    ]
    common_verify(client, expected_keys)


NESTED_KEYS_USERDATA = _USERDATA.format(
    bootcmd=(
        "sed -i 's;#AuthorizedKeysFile.*;AuthorizedKeysFile "
        "/etc/ssh/authorized_keys %h/foo/bar/ssh/keys;' "
        "/etc/ssh/sshd_config"
    )
)


@pytest.mark.skipif(
    not IS_UBUNTU, reason="Tests permissions specific to Ubuntu releases"
)
@pytest.mark.skipif(
    PLATFORM == "qemu",
    reason="QEMU cloud manually adding key interferes with test",
)
@pytest.mark.user_data(NESTED_KEYS_USERDATA)
def test_nested_keys(client: IntegrationInstance):
    expected_keys = [
        ("test_user1", "/home/test_user1/foo/bar/ssh/keys", TEST_USER1_KEYS),
        ("test_user2", "/home/test_user2/foo/bar/ssh/keys", TEST_USER2_KEYS),
        ("ubuntu", "/home/ubuntu/foo/bar/ssh/keys", TEST_DEFAULT_KEYS),
        ("root", "/root/foo/bar/ssh/keys", TEST_DEFAULT_KEYS),
    ]
    common_verify(client, expected_keys)


EXTERNAL_KEYS_USERDATA = _USERDATA.format(
    bootcmd=(
        "sed -i 's;#AuthorizedKeysFile.*;AuthorizedKeysFile "
        "/etc/ssh/authorized_keys /etc/ssh/authorized_keys/%u/keys;' "
        "/etc/ssh/sshd_config"
    )
)


@pytest.mark.skipif(
    not IS_UBUNTU, reason="Tests permissions specific to Ubuntu releases"
)
@pytest.mark.skipif(
    PLATFORM == "qemu",
    reason="QEMU cloud manually adding key interferes with test",
)
@pytest.mark.user_data(EXTERNAL_KEYS_USERDATA)
def test_external_keys(client: IntegrationInstance):
    expected_keys = [
        (
            "test_user1",
            "/etc/ssh/authorized_keys/test_user1/keys",
            TEST_USER1_KEYS,
        ),
        (
            "test_user2",
            "/etc/ssh/authorized_keys/test_user2/keys",
            TEST_USER2_KEYS,
        ),
        ("ubuntu", "/etc/ssh/authorized_keys/ubuntu/keys", TEST_DEFAULT_KEYS),
        ("root", "/etc/ssh/authorized_keys/root/keys", TEST_DEFAULT_KEYS),
    ]
    common_verify(client, expected_keys)
