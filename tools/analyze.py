#!/usr/bin/env python3

import parse

from statistics import mean, median, stdev
from dataclasses import dataclass
from pprint import pprint

FIRST = 0
LAST = 30
DIR = "./out/"
SERIES = "oracular"
TYPE = "vm"


@dataclass
class Statistics:
    mean: float
    median: float
    range: float
    stdev: float
    name: str


def iter_files(output_type: str, instrumented_type: str):
    """iterate output files

    output_type: analyze, blame, chain-fuzz, chain, dump
    instrumented_type: cached, divide-conquer-enabled, divide-conquer-disabled, first-boot
    """
    for i in range(FIRST, LAST):
        yield f"{DIR}/{i}/{SERIES}-{TYPE}/{instrumented_type}/{output_type}.txt"


def analyze_systemd_analyze(instrumented_type: str):
    analyze_times = []
    for file in iter_files("analyze", instrumented_type):
        with open(file) as f:
            analyze_times.append(parse.parse_systemd_analyze(f.read()).target)
    return Statistics(
        name=instrumented_type,
        mean=mean(analyze_times),
        median=median(analyze_times),
        range=max(analyze_times) - min(analyze_times),
        stdev=stdev(analyze_times),
    )


if __name__ == "__main__":
    # enabled cases
    #
    # print(analyze_systemd_analyze("cached")) # this measures enabled cloud-init, not useful
    # print(analyze_systemd_analyze("divide-conquer-enabled"))
    # print(analyze_systemd_analyze("disabled-no-generator"))

    print(analyze_systemd_analyze("no-op"))
    print(analyze_systemd_analyze("disabled"))
    print(analyze_systemd_analyze("modified-order-generalized-disabled"))
    print(analyze_systemd_analyze("modified-order-simplified-disabled"))

    # not measured recently
    # print(analyze_systemd_analyze("modified-order-simplified-no-op-disabled"))

    # old name
    # print(analyze_systemd_analyze("tako"))
