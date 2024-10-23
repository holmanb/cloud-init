#!/usr/bin/env python3
"""Basic local performance bootspeed testing using QEMU

Boot a control image in QEMU for certain number of sample boots and
extract bootspeed-related samples and logs via commands or logs such as:
 - systemd-analyze, systemd-analyze critical-chain, cloud-init analyze
 - journalctl, /var/log/cloud-init.log.

Create a derivative image with cloud-init upgraded in QEMU which has not yet
booted. Cloud-init can be installed either from a local deb or by providing
ppa:<custom_ppa>.

Launch multiple instances up to --number-of-launches of control and upgraded
cloudimages.

Persist all metrics artifacts in --data-dir as JSON files.
Calculate averages across all control runs and upgraded image samples.

Highlight deltas between control verus upgraded averages which
are greater than 0.1 seconds different and 20 percent different.


REQUIREMENTS:
- sudo permissions to mount ISO images
- mount-image-callback utility from cloud-image-utils deb package
"""

# ruff: noqa: E501

import logging
import os
import shutil
import glob
import tempfile
import time
from argparse import ArgumentParser, FileType
from pathlib import Path

from cloudinit import subp
import pycloudlib

# Number of original and upgraded boots to perform and evaluate
BOOT_SAMPLES = 3

# Do not report performance deltas where average delta for service between
# control and new images is less than this number of seconds
MIN_AVG_DELTA = 0.1

# Do not report performance deltas for services where average delta between
# control and new is below this percentage
MIN_AVG_PERCENT_DELTA = 20


MANDATORY_REPORT_KEYS = (
    "time_cloudinit_total",
    "client_time_to_ssh",
)

DEFAULT_PPA = "ppa:cloud-init-dev/daily"


def retry_cmd(instance, cmd):
    while True:
        try:
            return instance.execute(cmd)
        except Exception:
            time.sleep(0.01)


def update_cloud_init_in_container_image(
    img_path: str, deb_path: str, suffix=".modified"
) -> str:
    alias = f"{img_path}{suffix}"
    temp_dir = tempfile.TemporaryDirectory()
    with temp_dir:
        build_container_image = [
            [
                "sudo",
                "mount",
                "-t",
                "proc",
                "/proc",
                "squashfs-root/proc/",
            ],
            [
                "sudo",
                "mount",
                "--bind",
                "/dev",
                "squashfs-root/dev/",
            ],
            [
                "sudo",
                "mount",
                "--bind",
                "/sys",
                "squashfs-root/sys/",
            ],
            [
                "sudo",
                "mount",
                "--bind",
                "/run",
                "./squashfs-root/run/",
            ],
            [
                "sudo",
                "mount",
                "--bind",
                "/tmp",
                "./squashfs-root/tmp/",
            ],
            [
                "sudo",
                "chroot",
                "squashfs-root/",
                "sh",
                "-c",
                "dpkg -i /cloud-init.deb",
            ],
            [
                "sudo",
                "umount",
                "./squashfs-root/proc",
            ],
            [
                "sudo",
                "umount",
                "./squashfs-root/dev/",
            ],
            [
                "sudo",
                "umount",
                "./squashfs-root/sys/",
            ],
            [
                "sudo",
                "umount",
                "./squashfs-root/run/",
            ],
            [
                "sudo",
                "umount",
                "./squashfs-root/tmp/",
            ],
        ]
        subp.subp(
            [
                "lxc",
                "image",
                "export",
                img_path,
                temp_dir.name,
            ]
        )
        # this should only have one file each
        squashfs = glob.glob(f"{temp_dir.name}/*.squashfs")[0]
        meta = glob.glob(f"{temp_dir.name}/*.tar.xz")[0]
        subp.subp(
            ["sudo", "unsquashfs", squashfs],
            cwd=temp_dir.name,
        )
        breakpoint()
        subp.subp(
            [
                "sudo",
                "cp",
                deb_path,
                f"{
                    temp_dir.name}/squashfs-root/cloud-init.deb",
            ]
        )
        for command in build_container_image:
            try:
                subp.subp(
                    command,
                    cwd=temp_dir.name,
                )
            except Exception as e:
                print(e)
                breakpoint()
                print(command)
        subp.subp(
            ["mksquashfs", "root", "new.squashfs"],
            cwd=temp_dir.name,
        )
        breakpoint()
        subp.subp(
            [
                "lxc",
                "image",
                "import",
                meta,
                img_path,
                temp_dir.name,
                f"--alias={alias}",
            ]
        )
    return alias


def update_cloud_init_in_vm_image(
    img_path: str, deb_path: str, suffix=".modified"
) -> str:
    """Use mount-image-callback to install a known deb into an image"""
    new_img_path = os.path.basename(img_path.replace(".img", f".img{suffix}"))
    new_img_path = f"{os.getcwd()}/{new_img_path}"
    shutil.copy(img_path, new_img_path)
    subp.subp(["sync"])
    if deb_path.endswith(".deb"):
        commands = [
            [
                "sudo",
                "mount-image-callback",
                new_img_path,
                "--",
                "sh",
                "-c",
                f"cp {deb_path} ${{MOUNTPOINT}}/.; chroot ${{MOUNTPOINT}} dpkg -i /{
                    os.path.basename(deb_path)}",
            ],
        ]
    elif deb_path.startswith("ppa:"):
        commands = [
            [
                "sudo",
                "mount-image-callback",
                "--system-mounts",
                "--system-resolvconf",
                new_img_path,
                "--",
                "sh",
                "-c",
                f"chroot ${{MOUNTPOINT}} add-apt-repository {
                    deb_path} -y; DEBIAN_FRONTEND=noninteractive chroot ${{MOUNTPOINT}} apt-get install -o  Dpkg::Options::='--force-confold' cloud-init -y",
            ],
        ]
    else:
        raise RuntimeError(
            f"Invalid deb_path provided: {
                deb_path}. Expected local .deb or"
            " ppa:"
        )
    for command in commands:
        subp.subp(command)
    return new_img_path


def get_parser():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument(
        "--series",
        choices=[
            "bionic",
            "focal",
            "jammy",
            "lunar",
            "mantic",
            "noble",
            "oracular",
        ],
        help="Ubuntu series to test",
    )
    parser.add_argument(
        "--package",
        dest="package",
        help=("Deb path from which to install cloud-init for testing."),
    )
    parser.add_argument(
        "--platform",
        dest="platform",
        choices=list(PLATFORM_FROM_STR.keys()),
        help=("Cloud platform to build image for"),
    )
    parser.add_argument(
        "--image",
        dest="image",
        default="",
        help=("Image to build from, defaults to daily image"),
    )
    return parser


def assert_dependencies():
    """Fail on any missing dependencies."""
    if not all(
        [shutil.which("mount-image-callback"), shutil.which("unsquashfs")]
    ):
        raise RuntimeError(
            "Missing mount-image-callback utility. "
            "Try: apt-get install cloud-image-utils"
        )


PLATFORM_FROM_STR = {
    "qemu": pycloudlib.Qemu,
    "gce": pycloudlib.GCE,
    "ec2": pycloudlib.EC2,
    "lxd_container": pycloudlib.LXDContainer,
    "lxd_vm": pycloudlib.LXDVirtualMachine,
}


def build_image(deb_path: str, series: str, platform: str, image: str):
    with PLATFORM_FROM_STR[platform](tag="examples") as cloud:
        daily = image or cloud.daily_image(release=series)
        print(
            f"--- Creating modified daily image {daily} with cloud-init"
            f" from {deb_path}"
        )
        if isinstance(cloud, pycloudlib.LXDContainer):
            out = update_cloud_init_in_container_image(
                daily, deb_path, suffix=1
            )
        else:
            out = update_cloud_init_in_vm_image(daily, deb_path, suffix=1)
        print(out)
        return out


if __name__ == "__main__":
    assert_dependencies()
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("paramiko").setLevel(logging.WARNING)
    logging.getLogger("qemu.qmp.protocol").setLevel(logging.WARNING)
    logging.getLogger("pycloudlib").setLevel(logging.INFO)
    logging.getLogger("paramiko.transport:Auth").setLevel(logging.INFO)
    parser = get_parser()
    args = parser.parse_args()
    build_image(
        args.package,
        series=args.series,
        platform=args.platform,
        image=args.image,
    )
