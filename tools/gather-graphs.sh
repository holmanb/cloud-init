#!/usr/bin/env bash
# shellcheck disable=SC2086

# debug mode, print output
#if test $1 -eq "-d"; then
set -x
#fi
#if test $1 -eq "-s"; then
#    DELETE=""
#else
#    DELETE="lxc rm -f $INSTANCE"
#fi

# fail if a command fails
set -e

# LAUNCH is the default launch command
LAUNCH="lxc launch ubuntu:oracular oracular"

# OUT is the output directory for files
OUT=out/

# CLOUD_INIT is the cloud-init target
CLOUD_INIT=cloud-init

# MULTI_USER is the default target
MULTI_USER=multi-user


# wait_for_target manually waits since cloud-init status --wait is insufficient:
# 1) `lxc exec` fails when vm isn't booted yet
# 2) `cloud-init status --wait` expects fs artifacts which don't exist when services
#    are overridden.
function wait_for_target(){
    local INSTANCE=$1
    local TARGET=$2
    local total=0
    while true; do
        # work around exec before dbus is available
        set +e
        lxc exec $INSTANCE -- systemctl is-active $TARGET.target 2>/dev/null
        local rc=$?
        set -e
        if [ $rc = 1 ]; then
            echo "waiting for dbus ${total}s"
        elif [ $rc = 3 ]; then
            echo "waiting for dbus ${total}s"
        elif [ $rc = 255 ];then
            echo "vm not booted yet ${total}s"
        # manually check if cloud-init.target is active yet
        elif lxc exec $INSTANCE -- systemctl is-active $TARGET.target | grep active; then
            return
        else
            echo "inactive ${total}s"
        fi
        sleep 1
        total=$(( total + 1))
        if [[ $total -ge 150 ]]; then
            notify "getting slow"
        fi
    done
}

function gather(){
    local INSTANCE=$1
    local OUT=$2
    local FLAVOR=$3
    # defaults to gathering for multi-user.target
    lxc exec $INSTANCE -- systemd-analyze dot > $OUT/dot-$INSTANCE-$FLAVOR.dot
    lxc exec $INSTANCE -- systemd-analyze dot --order > $OUT/dot-order-$INSTANCE-$FLAVOR.dot
    lxc exec $INSTANCE -- systemd-analyze critical-chain > $OUT/chain-$INSTANCE-$FLAVOR.txt
    lxc exec $INSTANCE -- systemd-analyze critical-chain --fuzz=60> $OUT/chain-fuzz-$INSTANCE-$FLAVOR.txt
    lxc exec $INSTANCE -- systemd-analyze > $OUT/analyze-$INSTANCE-$FLAVOR.txt
    lxc exec $INSTANCE -- systemd-analyze blame > $OUT/blame-$INSTANCE-$FLAVOR.txt
}

function run_test(){
    # launch once, to avoid differences due to caching effects (i.e. snapd')
    local INSTANCE="$1"
    local COMMAND="$2"
    local OVERRIDE=$(mktemp)
    local OVERRIDE_MAIN=$(mktemp)
    local MAIN_D=/etc/systemd/system/cloud-init-main.service.d/
    eval "$COMMAND"

    # gather first-boot data
    wait_for_target $INSTANCE $CLOUD_INIT
    gather $INSTANCE $OUT first-boot

    # re-run: cached
    lxc exec $INSTANCE -- cloud-init clean --machine-id --logs --configs all
    lxc stop $INSTANCE

    # gather cached data
    lxc start $INSTANCE
    wait_for_target $INSTANCE $CLOUD_INIT
    gather $INSTANCE $OUT cleaned

    # override services with no-ops
    printf '[Service]\nExecStart=\nExecStart=true\n' > $OVERRIDE
    printf '[Service]\nExecStart=\nExecStart=systemd-notify --ready --status "done"\n' > $OVERRIDE_MAIN
    for DIR in /etc/systemd/system/cloud-init-local.service.d/ /etc/systemd/system/cloud-init-network.service.d/ /etc/systemd/system/cloud-config.service.d/ /etc/systemd/system/cloud-final.service.d/; do
        lxc exec $INSTANCE -- mkdir $DIR
        lxc file push $OVERRIDE $INSTANCE/$DIR/override.conf
    done
    lxc exec $INSTANCE -- mkdir $MAIN_D
    lxc file push $OVERRIDE_MAIN $INSTANCE/$MAIN_D/override.conf

    # re-run: no-op
    lxc exec $INSTANCE -- cloud-init clean --machine-id --logs --configs all
    lxc stop $INSTANCE

    # gather modified data
    lxc start $INSTANCE
    wait_for_target $INSTANCE $CLOUD_INIT
    gather $INSTANCE $OUT overridden

    # re-run: disabled
    lxc exec $INSTANCE -- cloud-init clean --machine-id --logs --configs all
    lxc exec $INSTANCE -- touch /etc/cloud/cloud-init.disabled
    lxc stop $INSTANCE

    # gather modified data
    lxc start $INSTANCE
    # in this case cloud-init.target is unavailable
    wait_for_target $INSTANCE $MULTI_USER
    gather $INSTANCE $OUT disabled
    eval $DELETE
}

mkdir -p $OUT
for INSTANCE in oracular oracular-vm; do
    if [[ $INSTANCE == *"-vm" ]]; then
        LAUNCH="$LAUNCH-vm --vm"
    fi
    run_test $INSTANCE "$LAUNCH"
done
