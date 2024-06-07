import json
import os
import pathlib
from textwrap import dedent
from contextlib import contextmanager
from tempfile import mkstemp

import pytest
from pycloudlib.lxd.instance import LXDInstance

from cloudinit.subp import subp
from tests.integration_tests.instances import IntegrationInstance
from tests.integration_tests.integration_settings import PLATFORM
from tests.integration_tests.releases import IS_UBUNTU



def setup_and_mount_lxd_disk(instance: LXDInstance) -> dict:
    subp(
        "lxc config device add {} test-disk-setup-disk disk source={}".format(
            instance.name, mkstemp()[1]
        ).split()
    )


@pytest.mark.skipif(not IS_UBUNTU, reason="Only ever tested on Ubuntu")
class TestGrowPart:
    """Test growpart"""

    @pytest.mark.lxd_setup.with_args(setup_and_mount_lxd_disk)
    @pytest.mark.skipif(
        PLATFORM != "lxd_vm", reason="Test requires additional mounted device"
    )
    @pytest.mark.user_data(
        dedent(
            """
            #cloud-config
            # Create undersized partition in bootcmd
            bootcmd:
              - parted /dev/sdb --script                \
                      mklabel gpt                       \
                      mkpart primary 0 1MiB
              - parted /dev/sdb --script print
            growpart:
              devices:
              - "/"
              - "/dev/sdb1"
            runcmd:
              - parted /dev/sdb --script print
            """
        )
    )
    def test_grow_part_lxd(self, client: IntegrationInstance):
        """Verify growpart on device passed via lxd"""
        log = client.read_from_file("/var/log/cloud-init.log")
        assert (
            "cc_growpart.py[INFO]: '/dev/sdb1' resized:"
            " changed (/dev/sdb1) from" in log
        )

        lsblk = json.loads(client.execute("lsblk --json"))
        sdb = [x for x in lsblk["blockdevices"] if x["name"] == "sdb"][0]
        assert len(sdb["children"]) == 1
        assert sdb["children"][0]["name"] == "sdb1"
        assert sdb["size"] == "16M"

    @pytest.mark.skipif(
        PLATFORM != "lxd_vm", reason="Test requires more memory than normal"
    )
    @pytest.mark.lxd_config_dict(
            {"limits.memory": "4GB"}
    )
    @pytest.mark.lxd_use_exec
    @pytest.mark.user_data(
        dedent(
            """
            #cloud-config
            runcmd:
              # easiest to remove all squashfs mounts by removing snapd
              - apt -y purge snapd
            """
        )
    )
    def test_grow_part_generic(self, client: IntegrationInstance):
        """

        Note: The following operations look similar to manual chroot creation.
        They share many things, but this differs in one fundamental way:

            The goal of this is to _destroy_ the original root filesystem.

        This is why the following does a pivot_root rather than a chroot.
        In order to destroy the original root filesystem, this test does the
        following:
            1) create a new temporary root filesystem
            2) switch to the new temporary root filesystem
            3) reload processes with new root filesystem
            4) create a shrunken partition and new filesystem on that partition
            5) switch to the new persistent root filesystem
            6) destroy the temporary fs
            7) verify that cloud-init grows the root partition and filesystem
        """

        # wait until complete
        assert client.execute("cloud-init status --wait")

        # clean the instance
        assert client.execute("cloud-init clean --logs")

        # 1) create a new temporary root filesystem
        # unmount unnecessary filesystems
        client.execute("umount -a")

        # create mountpoint for temporary root filesystem
        assert client.execute("mkdir /tmp/tmproot/")

        # mount temporary root filesystem
        assert client.execute("mount -t tmpfs none /tmp/tmproot")

        # create special directories for new filesystem mountpoints
        assert client.execute("bash -c 'mkdir -p /tmp/tmproot/{proc,sys,dev,run,usr,var,tmp,oldroot}'")

        # copy everything to temporary root filesystem
        assert client.execute(
            "bash -c 'cp -ax / /tmp/tmproot/'"
        )

        # make systemd play nicely with pivot root
        assert client.execute("mount --make-rprivate /")

        # 2) switch to the new temporary root filesystem
        # switch to new root directory
        assert client.execute("pivot_root /tmp/tmproot /tmp/tmproot/oldroot")

        # move special kernel directory mounts
        assert client.execute("for i in dev proc sys run; do mount --move /oldroot/$i /$i; done")

        # 3) reload processes with new root filesystem
        # need to restart or kill all remaining processes which access the old
        # filesystem and disk
        # the following command would do it, except pycloudlib's shell quoting / encoding code is borked
        # "systemctl | grep running | awk '{print $1}' | grep -v tty | grep '\.service$' | xargs systemctl restart"
        # Manually work around it.
        for line in client.execute("systemctl").stdout.split("\n"):
            if "running" in line and ".service" in line and "tty" not in line:
                service = line.split()[0]
                client.execute(f"systemctl restart {service}")

        # probably not necessary but will make this test more resilient
        assert client.execute("systemctl disable multipathd --now ")

        # reload systemd with the new root
        assert client.execute("systemctl daemon-reexec")

        # force sd-pam to reload (this will remain using the old root even
        # after systemd restarts)
        assert client.execute("systemctl stop user.slice")

        # kill remaining users of the old root
        client.execute("fuser -km -9 /oldroot")

        # kill remaining users of the disk
        client.execute("fuser -km -9 /dev/sda1")

        # unmount the old filesystem
        #
        # if this fails, check for remaining references to both /oldroot
        # and /dev/sda1 via:
        # fuser -vm /oldroot
        # fuser -vm /dev/sda1
        # in /proc/mounts
        # in the filemaps / file descriptors of existing processes under /proc
        assert client.execute("umount /oldroot")

        # 4) shrink the partition and create a new filesystem on that partition
        # reformat the disk
        # https://bugs.launchpad.net/ubuntu/+source/parted/+bug/1270203
        assert client.execute(
            "yes|parted ---pretend-input-tty /dev/sda resizepart 1 4GiB"
        )
        assert client.execute("partprobe")
        # kill remaining users of the disk (from parted)
        client.execute("fuser -km -9 /dev/sda1")
        client.execute("fuser -km -9 /oldroot")
        client.execute("fuser -km -9 /dev/sda1")
        client.execute("fuser -km -9 /oldroot")
        assert client.execute("mkfs.xfs -f /dev/sda1")

        # create mountpoint for temporary root filesystem
        assert client.execute("mkdir /tmp/tmproot")

        # mount temporary root filesystem
        assert client.execute("mount /dev/sda1 /tmp/tmproot")

        # create special directories for new filesystem mountpoints
        assert client.execute("bash -c 'mkdir -p /tmp/tmproot/{proc,sys,dev,run,usr,var,tmp,oldroot}'")

        # copy everything to temporary root filesystem
        assert client.execute(
            "bash -c 'cp -ax / /tmp/tmproot/'"
        )

        # make systemd play nicely with pivot root
        assert client.execute("mount --make-rprivate /")

        # 5) switch to the new persistent root filesystem
        # switch to new root directory
        assert client.execute("pivot_root /tmp/tmproot /tmp/tmproot/oldroot")

        # move special kernel directory mounts
        assert client.execute("for i in dev proc sys run; do mount --move /oldroot/$i /$i; done")

        # need to restart or kill all remaining processes which access the old
        # filesystem and disk
        # the following command would do it, except pycloudlib's shell quoting / encoding code is borked
        # "systemctl | grep running | awk '{print $1}' | grep -v tty | grep '\.service$' | xargs systemctl restart"
        # Manually work around it.
        for line in client.execute("systemctl").stdout.split("\n"):
            if "running" in line and ".service" in line and "tty" not in line:
                service = line.split()[0]
                client.execute(f"systemctl restart {service}")

        # probably not necessary but will make this test more resilient
        assert client.execute("systemctl disable multipathd --now ")

        # reload systemd with the new root
        assert client.execute("systemctl daemon-reexec")

        # force sd-pam to reload (this will remain using the old root even
        # after systemd restarts)
        assert client.execute("systemctl stop user.slice")

        # kill remaining users of the old root
        # returns non-zero if files not accessed
        client.execute("fuser -km -9 /oldroot")

        # kill remaining users of the disk
        # returns non-zero if files not accessed
        client.execute("fuser -km -9 /dev/sda1")

        # unmount the old filesystem
        #
        # if this fails, check for remaining references to both /oldroot
        # and /dev/sda1 via:
        # fuser -vm /oldroot
        # fuser -vm /dev/sda1
        # in /proc/mounts
        # in the filemaps / file descriptors of existing processes under /proc
        assert client.execute("umount /oldroot")

        # 6) verify that cloud-init grows the root partition and filesystem
        client.execute("cloud-init init --local")
        client.execute("cloud-init init")
        log = client.read_from_file("/var/log/cloud-init.log")
        assert (
            "cc_growpart.py[INFO]: '/dev/sda1' resized:"
            " changed (/dev/sdb1) from" in log
        )
        assert "Resized root filesystem" in log
