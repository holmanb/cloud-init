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

# CLOUD_INIT is the graphical target
GRAPHICAL=graphical

TAKO="tako"
# TAKO_BINARY_BINARY is the path of the pre-built tako binary
TAKO_LOCAL_BINARY=$HOME/upstream/tako/tako

TAKO_INSTALLED_PATH="/usr/libexec"

TAKO_INSTALLED_BINARY="$TAKO_INSTALLED_PATH/$TAKO"

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

    mkdir -p $OUT
    # defaults to gathering for multi-user.target
    lxc exec $INSTANCE -- systemd-analyze dot > $OUT/dot-total.dot 2>/dev/null
    lxc exec $INSTANCE -- systemd-analyze dot --order > $OUT/dot-order.dot 2>/dev/null
    lxc exec $INSTANCE -- systemd-analyze dot --require > $OUT/dot-require.dot 2>/dev/null
    lxc exec $INSTANCE -- systemd-analyze critical-chain > $OUT/chain.txt
    lxc exec $INSTANCE -- systemd-analyze critical-chain --fuzz=60> $OUT/chain-fuzz.txt
    lxc exec $INSTANCE -- systemd-analyze > $OUT/analyze.txt
    lxc exec $INSTANCE -- systemd-analyze blame > $OUT/blame.txt
    lxc exec $INSTANCE -- systemd-analyze dump > $OUT/dump.txt

    # remove the cache and reload the daemon
    #lxc exec $INSTANCE -- rm -rf /run/cloud-init/
    #lxc exec $INSTANCE -- time -o tmp systemctl daemon-reload
    #lxc exec $INSTANCE -- cat tmp > $OUT/uncached-reload.txt
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
    gather $INSTANCE "$OUT/$INSTANCE/$FLAVOR"
}

function gather_first_boot(){
    local INSTANCE="$1"
    local COMMAND="$2"
    eval "$COMMAND"

    # gather first-boot data
    wait_for_target $INSTANCE $CLOUD_INIT
    gather $INSTANCE "$OUT/$INSTANCE/first-boot"

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
    lxc exec $INSTANCE -- rm /etc/cloud/cloud-init.disabled
    for DIR in $LOCAL_D $NETWORK_D $CONFIG_D $FINAL_D; do
        lxc exec $INSTANCE -- rm -rf /$ETC_SYSTEM/$DIR
    done
    for DIR in $LOCAL_AFTER_D $NETWORK_AFTER_D $CONFIG_AFTER_D $FINAL_AFTER_D; do
        lxc exec $INSTANCE -- rm -rf /$ETC_SYSTEM/$DIR
    done
    # create "after services"
    lxc exec $INSTANCE -- rm -f /$LIB_SYSTEM/cloud-init-local-after.service
    lxc exec $INSTANCE -- rm -f /$LIB_SYSTEM/cloud-init-network-after.service
    lxc exec $INSTANCE -- rm -f /$LIB_SYSTEM/cloud-config-after.service
    lxc exec $INSTANCE -- rm -f /$LIB_SYSTEM/cloud-final-after.service
    lxc exec $INSTANCE -- rm -rf /$LIB_SYSTEM/$MAIN_D
}

function install_file(){
    local TEMP_SERVICE=$(mktemp)
    local INSTANCE="$1"
    local FILE_NAME="$2"
    local CONTENT="$3"
    echo "$CONTENT" > $TEMP_SERVICE
    lxc file push $TEMP_SERVICE $INSTANCE/$LIB_SYSTEM/$FILE_NAME
}

function service_template(){
    local ORDER="$1"
    cat << EOD
[Unit]
Description=tako client wrapper service: $ORDER
DefaultDependencies=no
$ORDER
# depends on the tako socket
After=$TAKO.service
Conflicts=shutdown.target

[Service]
Type=oneshot
Environment="TAKO=/run/tako/"
ExecStart=$TAKO_INSTALLED_BINARY notify tako.d/systemd order=$ORDER

[Install]
WantedBy=tako.target
EOD
}

# NOTE: to check for notices run:
#
#     tako notices --key=tako.d/systemd
function gather_tako_disabled(){
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
    local PERSISTENT_TEMP="/usr/local/temp/"
    local INSTANCE="$1"
    local OVERRIDE_AFTER=$(mktemp)
    local OVERRIDE_BEFORE=$(mktemp)
    local OVERRIDE_MAIN=$(mktemp)

    # setup
    #
    # remove all services
    lxc exec $INSTANCE -- mkdir -p $PERSISTENT_TEMP
    lxc exec $INSTANCE -- mv /$LIB_SYSTEM/cloud-init-main.service $PERSISTENT_TEMP
    lxc exec $INSTANCE -- mv /$LIB_SYSTEM/cloud-init-local.service $PERSISTENT_TEMP
    lxc exec $INSTANCE -- mv /$LIB_SYSTEM/cloud-init-network.service $PERSISTENT_TEMP
    lxc exec $INSTANCE -- mv /$LIB_SYSTEM/cloud-config.service $PERSISTENT_TEMP
    lxc exec $INSTANCE -- mv /$LIB_SYSTEM/cloud-final.service $PERSISTENT_TEMP

    local tako_daemon=$(cat << EOD
[Unit]
Description=tako service
DefaultDependencies=no

# it might be possible to run without the next line, but I would need to
# double check that /run/ is available for writing before, during, and after remounting filesystems
# and this also risks some really weird bugs
After=systemd-remount-fs.service
Before=shutdown.target
Conflicts=shutdown.target

[Service]
Environment="TAKO=/run/tako/"
# This would probably eventually be of type notify, which is capable of
# more richly notifying the init system of current status.
Type=simple
ExecStartPre=mkdir -p /run/tako
ExecStart=$TAKO_INSTALLED_BINARY run
# a putrid hack to prevent races with other services
# Type=notify and sending a notify once socket is available is the right way to do this
ExecStartPost=sh -c "until [ -S /run/tako/.tako.socket ]; do sleep 0.005; done"

# Output needs to appear in instance console output
StandardOutput=kmsg

[Install]
WantedBy=tako.target
EOD
)
    local tako_target=$(cat << EOD
[Unit]
Description=Tako target

[Install]
WantedBy=multi-user.target
EOD
)

    install_file $INSTANCE $TAKO.service "$tako_daemon"
    install_file $INSTANCE tako.target "$tako_target"

    # strategy: ignore cloud-init services and duplicates
    #
    # client wrappers: local
    #
    # Wants=network-pre.target
    # After=hv_kvp_daemon.service
    # Before=network-pre.target
    # Before=shutdown.target
    # Before=sysinit.target
    install_file $INSTANCE tako-local-after-hv.service "$(service_template 'After=hv_kvp_daemon.service')"
    install_file $INSTANCE tako-local-before-sysinit.service "$(service_template 'Before=sysinit.target')"
    install_file $INSTANCE tako-local-before-network-pre.service "$(service_template 'Before=network-pre.target')"
    install_file $INSTANCE tako-local-before-shutdown.service "$(service_template 'Before=shutdown.target')"

    #
    # client wrappers: network
    #
    # After=systemd-networkd-wait-online.service
    # After=networking.service
    # Before=network-online.target
    # Before=sshd-keygen.service
    # Before=sshd.service
    # Before=systemd-user-sessions.service
    # Before=sysinit.target                      # duplicate
    # Before=shutdown.target                     # duplicate
    install_file $INSTANCE tako-network-after-networkd-wait-online.service "$(service_template 'After=systemd-networkd-wait-online.service')"
    install_file $INSTANCE tako-network-after-networking.service "$(service_template 'After=networking.service')"
    install_file $INSTANCE tako-network-before-network-online.service "$(service_template 'Before=network-online.target')"
    install_file $INSTANCE tako-network-before-sshd.service "$(service_template 'Before=ssh.service')"
    install_file $INSTANCE tako-network-before-user-sessions.service "$(service_template 'Before=systemd-user-sessions.service')"

    # BOUNTY: $50 (expires in 2030)
    #
    # I have literally never seen proof of the existence of this service.
    # If anyone has evidence of the history of this service (besides the commit that introduced it), please provide proof and payment method
    # to Brett Holman to collect your reward.
    install_file $INSTANCE tako-network-before-sshd-keygen.service "$(service_template 'Before=sshd-keygen.service')"

    #
    # client wrappers: config
    #
    # After=network-online.target
    install_file $INSTANCE tako-config-after-network-online.service "$(service_template 'After=network-online.target')"

    #
    # client wrappers: final
    #
    # After=network-online.target  # duplicate
    # After=time-sync.target
    # After=rc-local.service
    # After=multi-user.target
    # Before=apt-daily.service
    install_file $INSTANCE tako-final-after-time-sync.target "$(service_template 'After=time-sync.target')"
    install_file $INSTANCE tako-final-after-rc-local.service "$(service_template 'After=rc-local.service')"
    install_file $INSTANCE tako-final-after-multi-user.target "$(service_template 'After=multi-user.target')"
    install_file $INSTANCE tako-final-before-apt-daily.service "$(service_template 'Before=apt-daily.service')"

    lxc exec $INSTANCE -- systemctl daemon-reload
    lxc exec $INSTANCE -- systemctl enable tako.target

    # enable cloud-init - no [Install] section, so fry an egg with a magnifying glass
    lxc exec $INSTANCE -- ln -s /lib/systemd/system/cloud-init.target /etc/systemd/system/multi-user.target.wants/cloud-init.target
    #lxc exec $INSTANCE -- ln -s /lib/systemd/system/tako.target /etc/systemd/system/multi-user.target.wants/tako.target

    # install tako binary
    lxc file push $TAKO_LOCAL_BINARY $INSTANCE/$TAKO_INSTALLED_PATH/

    # re-run: no-op
    clean_rerun $INSTANCE "tako" $GRAPHICAL
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
    for DIR in $LOCAL_D $NETWORK_D $CONFIG_D $FINAL_D; do
        lxc exec $INSTANCE -- rm -rf /$ETC_SYSTEM/$DIR
    done
    for DIR in $LOCAL_AFTER_D $NETWORK_AFTER_D $CONFIG_AFTER_D $FINAL_AFTER_D; do
        lxc exec $INSTANCE -- rm -rf /$ETC_SYSTEM/$DIR
    done
    # delete "after services"
    lxc exec $INSTANCE -- rm -f /$LIB_SYSTEM/cloud-init-local-after.service
    lxc exec $INSTANCE -- rm -f /$LIB_SYSTEM/cloud-init-network-after.service
    lxc exec $INSTANCE -- rm -f /$LIB_SYSTEM/cloud-config-after.service
    lxc exec $INSTANCE -- rm -f /$LIB_SYSTEM/cloud-final-after.service

    # override main
    lxc exec $INSTANCE -- rm -rf /$LIB_SYSTEM/$MAIN_D
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

    #gather_no_ops $INSTANCE
    #gather_disabled $INSTANCE
    #gather_generator_no_op $INSTANCE
    #gather_cached $INSTANCE
    #gather_divide_conquer_disabled $INSTANCE
    #gather_divide_conquer_enabled $INSTANCE
    gather_tako_disabled $INSTANCE

    lxc rm -f $INSTANCE
}

function main(){
    # seeking statistical significance
    for ITER in $(seq 0 30); do
        mkdir -p $RESULTS/$ITER
        echo "running iteration: $ITER"
        for TYPE in container; do #vm; do  # container; do
            INSTANCE="$SERIES-$TYPE"
            if [[ $INSTANCE == "vm" ]]; then
                COMMAND="$LAUNCH $INSTANCE --vm"
            else
                COMMAND="$LAUNCH $INSTANCE"
            fi
            lxc rm -f $INSTANCE 2>/dev/null || true
            run_test $INSTANCE "$COMMAND" $RESULTS/$ITER
        done
    done
}

if [ "$1" = "run" ]; then
    main
fi
