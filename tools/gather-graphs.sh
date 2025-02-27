#!/usr/bin/env bash

set -ex

VM="--vm"
LAUNCH="lxc launch ubuntu:oracular oracular"
SERVICE_D="/etc/systemd/system/cloud-init-local.service.d/ /etc/systemd/system/cloud-init-network.service.d/ /etc/systemd/system/cloud-config.service.d/ /etc/systemd/system/cloud-final.service.d/"
MAIN_D=/etc/systemd/system/cloud-init-main.service.d/
OUT=out/



# wait_for_cloud_init implements a manual wait since cloud-init status --wait is insufficient
# 1) `cloud-init status --wait` expects fs artifacts which don't exist when services
#    are overridden.
# 2) `lxc exec` fails when vm isn't booted yet
function wait_for_cloud_init(){
    local INSTANCE=$1
    while true; do
        # work around exec before dbus is available
        set +ex
        lxc exec $INSTANCE -- systemctl is-active cloud-init.target 2>/dev/null
        local rc=$?
        set -ex
        if [ $rc = 1 ]; then
            echo "no dbus"
        elif [ $rc = 3 ]; then
            echo "no dbus"
        elif [ $rc = 255 ];then
            echo "vm not booted yet"
        # manually check if cloud-init.target is active yet
        elif lxc exec $INSTANCE -- systemctl is-active cloud-init.target | grep active; then
            return
        else
            echo "inactive"
        fi
        sleep 1
    done
}

function gather(){
    local INSTANCE=$1
    local OUT=$2
    local FLAVOR=$3
    lxc exec $INSTANCE -- systemd-analyze dot multi-user.target > $OUT/dot-$INSTANCE-$FLAVOR.dot
    lxc exec $INSTANCE -- systemd-analyze critical-chain > $OUT/chain-$INSTANCE-$FLAVOR.txt
    lxc exec $INSTANCE -- systemd-analyze > $OUT/analyze-$INSTANCE-$FLAVOR.txt
}

function run_test(){
    # launch once, to avoid differences due to caching effects (i.e. snapd')
    local INSTANCE="$1"
    local COMMAND="$2"
    local OVERRIDE=$(mktemp)
    local OVERRIDE_MAIN=$(mktemp)
    eval "$COMMAND"

    # gather first-boot data
    wait_for_cloud_init $INSTANCE
    gather $INSTANCE $OUT first-boot

    # re-run
    lxc exec $INSTANCE -- cloud-init clean --machine-id --logs --configs all
    lxc stop $INSTANCE

    # gather cached data
    lxc start $INSTANCE
    wait_for_cloud_init $INSTANCE
    gather $INSTANCE $OUT cleaned

    # override services with no-ops
    printf '[Service]\nExecStart=\nExecStart=true\n' > $OVERRIDE
    printf '[Service]\nExecStart=\nExecStart=systemd-notify --ready --status "done"\n' > $OVERRIDE_MAIN
    for DIR in $SERVICE_D; do
        lxc exec $INSTANCE -- mkdir $DIR
        lxc file push $OVERRIDE $INSTANCE/$DIR/override.conf
    done
    lxc exec $INSTANCE -- mkdir $MAIN_D
    lxc file push $OVERRIDE_MAIN $INSTANCE/$MAIN_D/override.conf

    # re-run
    lxc exec $INSTANCE -- cloud-init clean --machine-id --logs --configs all
    lxc stop $INSTANCE

    # gather modified data
    lxc start $INSTANCE
    wait_for_cloud_init $INSTANCE
    gather $INSTANCE $OUT overridden
    lxc rm -f $INSTANCE
}

mkdir -p $OUT
for INSTANCE in oracular oracular-vm; do
    if [[ $INSTANCE == *"-vm" ]]; then
        LAUNCH="$LAUNCH-vm $VM"
    fi
    run_test $INSTANCE "$LAUNCH"
done
