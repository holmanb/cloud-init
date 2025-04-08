#!/usr/bin/env python3
import re
from pprint import pprint
from typing import Optional
from dataclasses import dataclass


@dataclass
class SystemdAnalyze:
    startup: float
    target: float


def parse_systemd_analyze(data: str):
    """
    Parses output of systemd-analyze

    Input:

        "Startup finished in 2.420s (userspace)\ngraphical.target reached after 2.389s in userspace."

    Returns:

        SystemdAnalyze(startup=2.420, target=2.389)
    """
    startup = re.search(r"Startup finished in ([\d.]+)s", data)
    target = re.search(r"graphical\.target reached after ([\d.]+)s", data)
    if not startup or not target:
        raise ValueError(f"Couldn't parse data: {data}")
    return SystemdAnalyze(
        startup=float(startup.group(1)), target=float(target.group(1))
    )


@dataclass
class DaemonReload:
    real: float
    user: float
    sys: float


def parse_daemon_reloads(data: str):
    """
    Parses output of systemctl daemon-reload

    Input:

        real 0.75
        user 0.00
        sys 0.02
        real 0.74
        user 0.00
        sys 0.03

    Returns:

        [
            DaemonReload(real=0.75, user=0.00, sys=0.02),
            DaemonReload(real=0.74, user=0.00, sys=0.03),
        ]
    """
    lines = data.strip().splitlines()
    if len(lines) % 3 != 0:
        raise ValueError(f"Couldn't parse data: {data}")
    reloads = []
    for i in range(0, len(lines), 3):
        real_parts = lines[i].split()
        user_parts = lines[i + 1].split()
        sys_parts = lines[i + 2].split()
        assert (
            real_parts[0] == "real"
        ), f"Expected 'real' but got {real_parts[0]}"
        assert (
            user_parts[0] == "user"
        ), f"Expected 'user' but got {user_parts[0]}"
        assert sys_parts[0] == "sys", f"Expected 'sys' but got {sys_parts[0]}"
        reloads.append(
            DaemonReload(
                real=float(lines[i].split()[1]),
                user=float(lines[i + 1].split()[1]),
                sys=float(lines[i + 2].split()[1]),
            )
        )
    return reloads


def parse_analyze_blame(data: str) -> dict[str, int]:
    """
    Parses output of systemd-analyze blame

    Input:
        "1.134s systemd-networkd-wait-online.service
         459ms systemd-udev-trigger.service
         383ms snapd.seeded.service"

    Returns:
        {
            'systemd-networkd-wait-online.service': 1134,
            'systemd-udev-trigger.service': 459,
            'snapd.seeded.service': 383,
        }

    """
    lines = data.strip().splitlines()
    blame = {}
    for line in lines:
        parts = line.strip().split()
        if len(parts) != 2:
            raise ValueError(f"Couldn't parse line: {line}")
        time_in_ms = int(parts[0].replace(".", "").rstrip("ms"))
        blame[parts[1]] = time_in_ms
    return blame


def parse_critical_chain(data: str) -> dict[str, tuple[int, Optional[int]]]:
    """
    Parses output of systemd-analyze critical-chain

    Input:
        "graphical.target @1.989s
         └─multi-user.target @1.989s
           └─systemd-user-sessions.service @1.988s +1ms
             └─local-fs.target @1.988s
               └─run-user-1000-gvfs.mount @2.249s
                 └─gvfs-daemon.service @2.249s +2ms"

    Returns:
        {
            'graphical.target': (1989, None),
            'multi-user.target': (1989, None),
            'systemd-user-sessions.service': (1988, 1),
            'local-fs.target': (1988, None),
            'run-user-1000-gvfs.mount': (2249, None),
            'gvfs-daemon.service': (2249, 2),
        }
    """
    lines = data.strip().splitlines()
    critical_chain = {}
    for line in lines:
        parts = line.strip(" ││─└─.").split()
        if not parts:
            continue  # ... line
        if len(parts) not in (2, 3):
            raise ValueError(f"Couldn't parse line: {line}")
        service_name = parts[0]
        assert parts[1].startswith("@"), f"Expected '@' but got {parts[1]}"
        active_time_in_ms = int(
            parts[1].replace(".", "").lstrip("@").rstrip("ms")
        )
        start_time_in_ms = None
        if len(parts) == 3:
            assert parts[2].startswith("+"), f"Expected '+' but got {parts[2]}"
            start_time_in_ms = int(
                parts[2].replace(".", "").lstrip("+").rstrip("ms")
            )
        critical_chain[service_name] = (active_time_in_ms, start_time_in_ms)
    return critical_chain


analyze = """\
Startup finished in 2.249s (userspace)
graphical.target reached after 1.989s in userspace
"""


daemon_reload = """\
real 0.75
user 0.00
sys 0.02
real 0.74
user 0.00
sys 0.03
"""

analyze_blame = """\
1.134s systemd-networkd-wait-online.service
 459ms systemd-udev-trigger.service
 383ms snapd.seeded.service
 249ms snapd.service
"""

critical_chain = """\
graphical.target @1.989s
└─multi-user.target @1.989s
  └─systemd-user-sessions.service @1.988s +1ms
    └─local-fs.target @1.988s
      └─run-user-1000-gvfs.mount @2.249s
        └─gvfs-daemon.service @2.249s +2ms
"""

pprint(parse_systemd_analyze(analyze))
pprint(parse_daemon_reloads(daemon_reload))
pprint(parse_analyze_blame(analyze_blame))
pprint(parse_critical_chain(critical_chain))
