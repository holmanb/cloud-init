#!/usr/bin/env bash
# shellcheck disable=SC2086

set -x

# fail if a command fails
set -e

# series to test
SERIES="oracular"

# LAUNCH is the default launch command
LAUNCH="lxc launch ubuntu:$SERIES"

# OUT is the output directory for files
RESULTS=out

# CLOUD_INIT is the cloud-init target
CLOUD_INIT=cloud-init

# wait_for_target manually waits since cloud-init status --wait is insufficient:
# 1) `lxc exec` fails when vm isn't booted yet
# 2) `cloud-init status --wait` expects fs artifacts which don't exist when services
#    are overridden.
# 3) sometimes we need to wait when cloud-init is disabled
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
        elif lxc exec $INSTANCE -- systemctl is-system-running | grep running; then
            return
        elif lxc exec $INSTANCE -- systemctl is-active $TARGET.target | grep active; then
            # manually check if cloud-init.target is active yet
            echo "almost booted, but not quite done"
        else
            echo "inactive ${total}s"
        fi
        sleep 1
        total=$(( total + 1))
        if [[ $total -ge 150 ]]; then
            notify "getting slow"
            lxc exec $INSTANCE -- sh -c "systemctl list-jobs"
        fi
    done
}

function gather(){
    local INSTANCE=$1
    local OUT=$2
    local FLAVOR=$3
    # defaults to gathering for multi-user.target
    lxc exec $INSTANCE -- systemd-analyze dot > $OUT/dot-$INSTANCE-$FLAVOR.dot 2>/dev/null
    lxc exec $INSTANCE -- systemd-analyze dot --order > $OUT/dot-order-$INSTANCE-$FLAVOR.dot 2>/dev/null
    lxc exec $INSTANCE -- systemd-analyze dot --require > $OUT/dot-require-$INSTANCE-$FLAVOR.dot 2>/dev/null
    lxc exec $INSTANCE -- systemd-analyze critical-chain > $OUT/chain-$INSTANCE-$FLAVOR.txt
    lxc exec $INSTANCE -- systemd-analyze critical-chain --fuzz=60> $OUT/chain-fuzz-$INSTANCE-$FLAVOR.txt
    lxc exec $INSTANCE -- systemd-analyze > $OUT/analyze-$INSTANCE-$FLAVOR.txt
    lxc exec $INSTANCE -- systemd-analyze blame > $OUT/blame-$INSTANCE-$FLAVOR.txt
    lxc exec $INSTANCE -- systemd-analyze dump > $OUT/dump-$INSTANCE-$FLAVOR.txt

    # remove the cache and reload the daemon
    lxc exec $INSTANCE -- rm -rf /run/cloud-init/

    lxc exec $INSTANCE -- time -o tmp systemctl daemon-reload
    lxc exec $INSTANCE -- cat tmp > $OUT/uncached-reload-$INSTANCE-$FLAVOR.txt
}

function clean_rerun(){
    local INSTANCE="$1"
    local FLAVOR=$2
    local WAIT_TARGET=$3

    lxc exec $INSTANCE -- cloud-init clean --machine-id --logs
    lxc stop $INSTANCE

    # gather cached data
    lxc start $INSTANCE
    wait_for_target $INSTANCE $WAIT_TARGET
    gather $INSTANCE $OUT $FLAVOR
}

function gather_first_boot(){
    local INSTANCE="$1"
    local COMMAND="$2"
    eval "$COMMAND"

    # gather first-boot data
    wait_for_target $INSTANCE $CLOUD_INIT
    gather $INSTANCE $OUT "first-boot"

}

function gather_cached(){
    # re-run: cached
    clean_rerun $INSTANCE "cached" $CLOUD_INIT
}

function push_file(){
    local INSTANCE="$1"
    local FILE="$2"
    local TMP_FILE="$3"
    lxc file push $TMP_FILE $INSTANCE/$FILE
}

function gather_no_ops(){
    local MAIN_D=/etc/systemd/system/cloud-init-main.service.d/
    local INSTANCE="$1"
    local OVERRIDE=$(mktemp)
    local OVERRIDE_MAIN=$(mktemp)
    #
    # setup
    #
    # override services with no-ops
    printf '[Service]\nExecStart=\nExecStart=true\n' > $OVERRIDE
    printf '[Service]\nExecStart=\nExecStart=systemd-notify --ready --status "done"\n' > $OVERRIDE_MAIN
    for DIR in /etc/systemd/system/cloud-init-local.service.d/ /etc/systemd/system/cloud-init-network.service.d/ /etc/systemd/system/cloud-config.service.d/ /etc/systemd/system/cloud-final.service.d/; do
        lxc exec $INSTANCE -- mkdir -p $DIR
        lxc file push $OVERRIDE $INSTANCE/$DIR/override.conf
    done
    lxc exec $INSTANCE -- mkdir -p $MAIN_D
    lxc file push $OVERRIDE_MAIN $INSTANCE/$MAIN_D/override.conf

    # re-run: no-op
    clean_rerun $INSTANCE "no-op" $CLOUD_INIT

    # teardown
    for DIR in /etc/systemd/system/cloud-init-local.service.d/ /etc/systemd/system/cloud-init-network.service.d/ /etc/systemd/system/cloud-config.service.d/ /etc/systemd/system/cloud-final.service.d/; do
        lxc exec $INSTANCE -- rm -rf $DIR
    done
}
function gather_divide_conquer_disabled(){
    local ETC_SYSTEM=etc/systemd/system
    local LIB_SYSTEM=lib/systemd/system
    local MAIN_D=cloud-init-main.service.d
    local LOCAL_D="cloud-init-local.service.d"
    local NETWORK_D="cloud-init-network.service.d"
    local CONFIG_D="cloud-config.service.d"
    local FINAL_D="cloud-final.service.d"
    local LOCAL_AFTER_D="cloud-init-local-after.service.d"
    local NETWORK_AFTER_D="cloud-init-network-after.service.d"
    local CONFIG_AFTER_D="cloud-config-after.service.d"
    local FINAL_AFTER_D="cloud-final-after.service.d"
    local INSTANCE="$1"
    local OVERRIDE_AFTER=$(mktemp)
    local OVERRIDE_BEFORE=$(mktemp)
    local OVERRIDE_MAIN=$(mktemp)
    #
    # setup
    #
    # override services with no-ops
    printf '[Unit]\nAfter=\n\n[Service]\nExecStart=\nExecStart=true\n' > $OVERRIDE_AFTER
    printf '[Unit]\nBefore=\n\n[Service]\nExecStart=\nExecStart=true\n' > $OVERRIDE_BEFORE
    lxc exec $INSTANCE -- touch /etc/cloud/cloud-init.disabled
    for DIR in $LOCAL_D $NETWORK_D $CONFIG_D $FINAL_D; do
        lxc exec $INSTANCE -- mkdir -p /$ETC_SYSTEM/$DIR
        lxc file push $OVERRIDE_BEFORE $INSTANCE/$ETC_SYSTEM/$DIR/override.conf
    done
    for DIR in $LOCAL_AFTER_D $NETWORK_AFTER_D $CONFIG_AFTER_D $FINAL_AFTER_D; do
        lxc exec $INSTANCE -- mkdir -p /$ETC_SYSTEM/$DIR
        lxc file push $OVERRIDE_AFTER $INSTANCE/$ETC_SYSTEM/$DIR/override.conf
    done
    # create "after services"
    lxc exec $INSTANCE -- cp /$LIB_SYSTEM/cloud-init-local.service /$LIB_SYSTEM/cloud-init-local-after.service
    lxc exec $INSTANCE -- cp /$LIB_SYSTEM/cloud-init-network.service /$LIB_SYSTEM/cloud-init-network-after.service
    lxc exec $INSTANCE -- cp /$LIB_SYSTEM/cloud-config.service /$LIB_SYSTEM/cloud-config-after.service
    lxc exec $INSTANCE -- cp /$LIB_SYSTEM/cloud-final.service /$LIB_SYSTEM/cloud-final-after.service

    # override main
    lxc exec $INSTANCE -- mkdir -p /$LIB_SYSTEM/$MAIN_D
    printf '[Service]\nExecStart=\nExecStart=systemd-notify --ready --status "done"\n' > $OVERRIDE_MAIN
    lxc file push $OVERRIDE_MAIN $INSTANCE/$LIB_SYSTEM/$MAIN_D/override.conf

    # re-run: no-op
    clean_rerun $INSTANCE "divide-conquer-disabled" $GRAPHICAL

    # teardown
    for DIR in /etc/systemd/system/cloud-init-local.service.d/ /etc/systemd/system/cloud-init-network.service.d/ /etc/systemd/system/cloud-config.service.d/ /etc/systemd/system/cloud-final.service.d/; do
        lxc exec $INSTANCE -- rm -rf $DIR
    done
}

function gather_divide_conquer_enabled(){
    local ETC_SYSTEM=etc/systemd/system
    local LIB_SYSTEM=lib/systemd/system
    local MAIN_D=cloud-init-main.service.d
    local LOCAL_D="cloud-init-local.service.d"
    local NETWORK_D="cloud-init-network.service.d"
    local CONFIG_D="cloud-config.service.d"
    local FINAL_D="cloud-final.service.d"
    local LOCAL_AFTER_D="cloud-init-local-after.service.d"
    local NETWORK_AFTER_D="cloud-init-network-after.service.d"
    local CONFIG_AFTER_D="cloud-config-after.service.d"
    local FINAL_AFTER_D="cloud-final-after.service.d"
    local INSTANCE="$1"
    local OVERRIDE_AFTER=$(mktemp)
    local OVERRIDE_BEFORE=$(mktemp)
    local OVERRIDE_MAIN=$(mktemp)
    #
    # setup
    #
    # override services with no-ops
    printf '[Unit]\nAfter=\n\n[Service]\nExecStart=\nExecStart=true\n' > $OVERRIDE_AFTER
    printf '[Unit]\nBefore=\n\n[Service]\nExecStart=\nExecStart=true\n' > $OVERRIDE_BEFORE
    for DIR in $LOCAL_D $NETWORK_D $CONFIG_D $FINAL_D; do
        lxc exec $INSTANCE -- mkdir -p /$ETC_SYSTEM/$DIR
        lxc file push $OVERRIDE_BEFORE $INSTANCE/$ETC_SYSTEM/$DIR/override.conf
    done
    for DIR in $LOCAL_AFTER_D $NETWORK_AFTER_D $CONFIG_AFTER_D $FINAL_AFTER_D; do
        lxc exec $INSTANCE -- mkdir -p /$ETC_SYSTEM/$DIR
        lxc file push $OVERRIDE_AFTER $INSTANCE/$ETC_SYSTEM/$DIR/override.conf
    done
    # create "after services"
    lxc exec $INSTANCE -- cp /$LIB_SYSTEM/cloud-init-local.service /$LIB_SYSTEM/cloud-init-local-after.service
    lxc exec $INSTANCE -- cp /$LIB_SYSTEM/cloud-init-network.service /$LIB_SYSTEM/cloud-init-network-after.service
    lxc exec $INSTANCE -- cp /$LIB_SYSTEM/cloud-config.service /$LIB_SYSTEM/cloud-config-after.service
    lxc exec $INSTANCE -- cp /$LIB_SYSTEM/cloud-final.service /$LIB_SYSTEM/cloud-final-after.service

    # override main
    lxc exec $INSTANCE -- mkdir -p /$LIB_SYSTEM/$MAIN_D
    printf '[Service]\nExecStart=\nExecStart=systemd-notify --ready --status "done"\n' > $OVERRIDE_MAIN
    lxc file push $OVERRIDE_MAIN $INSTANCE/$LIB_SYSTEM/$MAIN_D/override.conf

    # re-run: no-op
    clean_rerun $INSTANCE "divide-conquer-enabled" $CLOUD_INIT

    # teardown
    for DIR in /etc/systemd/system/cloud-init-local.service.d/ /etc/systemd/system/cloud-init-network.service.d/ /etc/systemd/system/cloud-config.service.d/ /etc/systemd/system/cloud-final.service.d/; do
        lxc exec $INSTANCE -- rm -rf $DIR
    done
}

function gather_disabled(){
    local INSTANCE="$1"
    # re-run: disabled

    # setup
    lxc exec $INSTANCE -- touch /etc/cloud/cloud-init.disabled

    # re-run: disabled
    clean_rerun $INSTANCE "disabled" $GRAPHICAL

    # teardown
    lxc exec $INSTANCE -- rm /etc/cloud/cloud-init.disabled
}

function gather_generator_no_op(){
    local GENERATOR="/lib/systemd/system-generators/cloud-init-generator"
    local BACKUP="/cloud-init-generator"
    local INSTANCE="$1"
    # setup
    lxc exec $INSTANCE -- cp $GENERATOR $BACKUP
    lxc exec $INSTANCE -- sed -i "s|/usr/lib/cloud-init/ds-identify|true|g" $GENERATOR
    lxc exec $INSTANCE -- chmod +x $GENERATOR
    lxc exec $INSTANCE -- chown root:root $GENERATOR
    # re-run: generator no-op
    clean_rerun $INSTANCE "generator-no-op" $CLOUD_INIT

    # teardown
    lxc exec $INSTANCE -- rm $GENERATOR
    lxc exec $INSTANCE -- mv $BACKUP $GENERATOR

}

function run_test(){
    # launch once, to avoid differences due to caching effects (i.e. snapd)
    local INSTANCE="$1"
    local COMMAND="$2"
    local OUT="$3"

    gather_first_boot $INSTANCE "$COMMAND"

    #gather_divide_conquer_disabled $INSTANCE
    #gather_divide_conquer_enabled $INSTANCE
    gather_no_ops $INSTANCE
    gather_disabled $INSTANCE
    # gather_generator_no_op $INSTANCE
    #gather_cached $INSTANCE

    lxc rm -f $INSTANCE
}

# seeking statistical significance
for ITER in $(seq 0 30); do
    mkdir -p $RESULTS/$ITER
    echo "running iteration: $ITER"
    for INSTANCE in $SERIES $SERIES-vm; do
        if [[ $INSTANCE == *"-vm" ]]; then
            COMMAND="$LAUNCH $INSTANCE --vm"
        else
            COMMAND="$LAUNCH $INSTANCE"
        fi
        lxc rm -f $INSTANCE 2>/dev/null || true
        run_test $INSTANCE "$COMMAND" $RESULTS/$ITER
    done
done
