#!/usr/bin/env bash
# shellcheck disable=SC2086

set -x

# fail if a command fails
set -e

# series to test
SERIES="oracular"

# LAUNCH is the default launch command
LAUNCH="lxc launch ubuntu:$SERIES"

# GENERATOR is the path to the cloud-init generator script
GENERATOR="/lib/systemd/system-generators/cloud-init-generator"

# GENERATOR_BACKUP is the temporary location that the generator is relocated to
GENERATOR_BACKUP="/cloud-init-generator"

# OUT is the output directory for files
RESULTS=out

# CLOUD_INIT is the cloud-init target
CLOUD_INIT=cloud-init

# CLOUD_INIT is the graphical target
GRAPHICAL=graphical

TAKO="tako"

# TAKO_LOCAL_BINARY is the path of the pre-built tako binary
TAKO_LOCAL_BINARY=$HOME/upstream/tako/tako

TAKO_INSTALLED_PATH="usr/libexec"

TAKO_INSTALLED_BINARY="$TAKO_INSTALLED_PATH/$TAKO"

TAKO_TARGET_CONTENT=$(cat << EOF
[Unit]
Description=Tako target

[Install]
WantedBy=multi-user.target
EOF
)

TAKO_DAEMON_SERVICE_CONTENT=$(cat << EOF
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
ExecStart=/$TAKO_INSTALLED_BINARY run
# a putrid hack to prevent races with other services
# Type=notify and sending a notify once socket is available is the right way to do this
ExecStartPost=sh -c "until [ -S /run/tako/.tako.socket ]; do sleep 0.005; done"

# Output needs to appear in instance console output
StandardOutput=kmsg

[Install]
WantedBy=tako.target
EOF
)

CLOUD_INIT_LOCAL_CONTENT=$(cat << EOF
[Unit]
# https://docs.cloud-init.io/en/latest/explanation/boot.html
Description=Cloud-init: Local Stage (pre-network)
DefaultDependencies=no
Wants=network-pre.target
Wants=tako.service
After=tako.service
Before=network-pre.target
Before=shutdown.target
Before=sysinit.target
Conflicts=shutdown.target
ConditionPathExists=!/etc/cloud/cloud-init.disabled
ConditionKernelCommandLine=!cloud-init=disabled
ConditionEnvironment=!KERNEL_CMDLINE=cloud-init=disabled

[Service]
Environment="TAKO=/run/tako/"
Type=oneshot
# This service is a shim which preserves systemd ordering while allowing a
# single Python process to run cloud-init's logic. This works by communicating
# with the cloud-init process over a unix socket to tell the process that this
# stage can start, and then wait on a return socket until the cloud-init
# process has completed this stage. The output from the return socket is piped
# into a shell so that the process can send a completion message (defaults to
# "done", otherwise includes an error message) and an exit code to systemd.
ExecStart=/$TAKO_INSTALLED_BINARY notify tako.d/systemd stage=cloud-init-local.service
RemainAfterExit=yes
TimeoutSec=0

# Output needs to appear in instance console output
StandardOutput=journal+console

[Install]
WantedBy=cloud-init.target
EOF
)

CLOUD_INIT_LOCAL_AFTER_CONTENT=$(cat << EOF
[Unit]
# https://docs.cloud-init.io/en/latest/explanation/boot.html
Description=Cloud-init: Local Stage (pre-network)
DefaultDependencies=no
Wants=network-pre.target
Wants=tako.service
After=tako.service
After=hv_kvp_daemon.service
Conflicts=shutdown.target
ConditionPathExists=!/etc/cloud/cloud-init.disabled
ConditionKernelCommandLine=!cloud-init=disabled
ConditionEnvironment=!KERNEL_CMDLINE=cloud-init=disabled

[Service]
Environment="TAKO=/run/tako/"
Type=oneshot
# This service is a shim which preserves systemd ordering while allowing a
# single Python process to run cloud-init's logic. This works by communicating
# with the cloud-init process over a unix socket to tell the process that this
# stage can start, and then wait on a return socket until the cloud-init
# process has completed this stage. The output from the return socket is piped
# into a shell so that the process can send a completion message (defaults to
# "done", otherwise includes an error message) and an exit code to systemd.
ExecStart=/$TAKO_INSTALLED_BINARY notify tako.d/systemd stage=cloud-init-local-after.service
RemainAfterExit=yes
TimeoutSec=0

# Output needs to appear in instance console output
StandardOutput=journal+console

[Install]
WantedBy=cloud-init.target
EOF
)

CLOUD_INIT_NETWORK_CONTENT=$(cat << EOF
[Unit]
# https://docs.cloud-init.io/en/latest/explanation/boot.html
Description=Cloud-init: Network Stage
DefaultDependencies=no
Wants=cloud-init-local.service
Wants=sshd-keygen.service
Wants=sshd.service
After=tako.service
Before=network-online.target
Before=sshd-keygen.service
Before=sshd.service
Before=systemd-user-sessions.service
Before=sysinit.target
Before=shutdown.target
Conflicts=shutdown.target
ConditionPathExists=!/etc/cloud/cloud-init.disabled
ConditionKernelCommandLine=!cloud-init=disabled
ConditionEnvironment=!KERNEL_CMDLINE=cloud-init=disabled

[Service]
Environment="TAKO=/run/tako/"
Type=oneshot
# This service is a shim which preserves systemd ordering while allowing a
# single Python process to run cloud-init's logic. This works by communicating
# with the cloud-init process over a unix socket to tell the process that this
# stage can start, and then wait on a return socket until the cloud-init
# process has completed this stage. The output from the return socket is piped
# into a shell so that the process can send a completion message (defaults to
# "done", otherwise includes an error message) and an exit code to systemd.
ExecStart=/$TAKO_INSTALLED_BINARY notify tako.d/systemd stage=cloud-init-network.service
RemainAfterExit=yes
TimeoutSec=0

# Output needs to appear in instance console output
StandardOutput=journal+console

[Install]
WantedBy=cloud-init.target
EOF
)

CLOUD_INIT_NETWORK_AFTER_CONTENT=$(cat << EOF
[Unit]
# https://docs.cloud-init.io/en/latest/explanation/boot.html
Description=Cloud-init: Network Stage
DefaultDependencies=no
Wants=cloud-init-local.service
Wants=sshd-keygen.service
Wants=sshd.service
After=tako.service
After=cloud-init-local.service
After=systemd-networkd-wait-online.service
After=networking.service
Conflicts=shutdown.target
ConditionPathExists=!/etc/cloud/cloud-init.disabled
ConditionKernelCommandLine=!cloud-init=disabled
ConditionEnvironment=!KERNEL_CMDLINE=cloud-init=disabled

[Service]
Environment="TAKO=/run/tako/"
Type=oneshot
# This service is a shim which preserves systemd ordering while allowing a
# single Python process to run cloud-init's logic. This works by communicating
# with the cloud-init process over a unix socket to tell the process that this
# stage can start, and then wait on a return socket until the cloud-init
# process has completed this stage. The output from the return socket is piped
# into a shell so that the process can send a completion message (defaults to
# "done", otherwise includes an error message) and an exit code to systemd.
ExecStart=/$TAKO_INSTALLED_BINARY notify tako.d/systemd stage=cloud-init-network-after.service
RemainAfterExit=yes
TimeoutSec=0

# Output needs to appear in instance console output
StandardOutput=journal+console

[Install]
WantedBy=cloud-init.target
EOF
)

CLOUD_INIT_CONFIG_CONTENT=$(cat << EOF
[Unit]
# https://docs.cloud-init.io/en/latest/explanation/boot.html
Description=Cloud-init: Config Stage
Wants=network-online.target cloud-config.target
After=tako.service
ConditionPathExists=!/etc/cloud/cloud-init.disabled
ConditionKernelCommandLine=!cloud-init=disabled
ConditionEnvironment=!KERNEL_CMDLINE=cloud-init=disabled

[Service]
Environment="TAKO=/run/tako/"
Type=oneshot
# This service is a shim which preserves systemd ordering while allowing a
# single Python process to run cloud-init's logic. This works by communicating
# with the cloud-init process over a unix socket to tell the process that this
# stage can start, and then wait on a return socket until the cloud-init
# process has completed this stage. The output from the return socket is piped
# into a shell so that the process can send a completion message (defaults to
# "done", otherwise includes an error message) and an exit code to systemd.
ExecStart=/$TAKO_INSTALLED_BINARY notify tako.d/systemd stage=cloud-config.service
RemainAfterExit=yes
TimeoutSec=0

# Output needs to appear in instance console output
StandardOutput=journal+console

[Install]
WantedBy=cloud-init.target
EOF
)

CLOUD_INIT_CONFIG_AFTER_CONTENT=$(cat << EOF
[Unit]
# https://docs.cloud-init.io/en/latest/explanation/boot.html
Description=Cloud-init: Config Stage
After=network-online.target cloud-config.target
After=tako.service
Wants=network-online.target cloud-config.target
ConditionPathExists=!/etc/cloud/cloud-init.disabled
ConditionKernelCommandLine=!cloud-init=disabled
ConditionEnvironment=!KERNEL_CMDLINE=cloud-init=disabled

[Service]
Environment="TAKO=/run/tako/"
Type=oneshot
# This service is a shim which preserves systemd ordering while allowing a
# single Python process to run cloud-init's logic. This works by communicating
# with the cloud-init process over a unix socket to tell the process that this
# stage can start, and then wait on a return socket until the cloud-init
# process has completed this stage. The output from the return socket is piped
# into a shell so that the process can send a completion message (defaults to
# "done", otherwise includes an error message) and an exit code to systemd.
ExecStart=/$TAKO_INSTALLED_BINARY notify tako.d/systemd stage=cloud-config-after.service
RemainAfterExit=yes
TimeoutSec=0

# Output needs to appear in instance console output
StandardOutput=journal+console

[Install]
WantedBy=cloud-init.target
EOF
)

CLOUD_INIT_FINAL_CONTENT=$(cat << EOF
[Unit]
# https://docs.cloud-init.io/en/latest/explanation/boot.html
Description=Cloud-init: Final Stage
After=tako.service
Before=apt-daily.service
Wants=network-online.target cloud-config.service
ConditionPathExists=!/etc/cloud/cloud-init.disabled
ConditionKernelCommandLine=!cloud-init=disabled
ConditionEnvironment=!KERNEL_CMDLINE=cloud-init=disabled


[Service]
Environment="TAKO=/run/tako/"
Type=oneshot
# This service is a shim which preserves systemd ordering while allowing a
# single Python process to run cloud-init's logic. This works by communicating
# with the cloud-init process over a unix socket to tell the process that this
# stage can start, and then wait on a return socket until the cloud-init
# process has completed this stage. The output from the return socket is piped
# into a shell so that the process can send a completion message (defaults to
# "done", otherwise includes an error message) and an exit code to systemd.
ExecStart=/$TAKO_INSTALLED_BINARY notify tako.d/systemd stage=cloud-init-final.service
RemainAfterExit=yes
TimeoutSec=0
TasksMax=infinity

# Output needs to appear in instance console output
StandardOutput=journal+console

[Install]
WantedBy=cloud-init.target
EOF
)

CLOUD_INIT_FINAL_AFTER_CONTENT=$(cat << EOF
[Unit]
# https://docs.cloud-init.io/en/latest/explanation/boot.html
Description=Cloud-init: Final Stage
After=tako.service
After=network-online.target time-sync.target cloud-config.service rc-local.service
After=multi-user.target
Wants=network-online.target cloud-config.service
ConditionPathExists=!/etc/cloud/cloud-init.disabled
ConditionKernelCommandLine=!cloud-init=disabled
ConditionEnvironment=!KERNEL_CMDLINE=cloud-init=disabled


[Service]
Environment="TAKO=/run/tako/"
Type=oneshot
# This service is a shim which preserves systemd ordering while allowing a
# single Python process to run cloud-init's logic. This works by communicating
# with the cloud-init process over a unix socket to tell the process that this
# stage can start, and then wait on a return socket until the cloud-init
# process has completed this stage. The output from the return socket is piped
# into a shell so that the process can send a completion message (defaults to
# "done", otherwise includes an error message) and an exit code to systemd.
ExecStart=/$TAKO_INSTALLED_BINARY notify tako.d/systemd stage=cloud-init-final-after.service
RemainAfterExit=yes
TimeoutSec=0
TasksMax=infinity

# Output needs to appear in instance console output
StandardOutput=journal+console

[Install]
WantedBy=cloud-init.target
EOF
)

DAEMON_RELOAD_CONTENT=$(cat << EOF
[Unit]
Description=Daemon reload
DefaultDependencies=no
Before=cloud-init-main.service

[Service]
ExecStart=systemctl daemon-reload

# Output needs to appear in instance console output
StandardOutput=journal+console

[Install]
WantedBy=cloud-init.target
EOF
)
#
# utility functions
#

# wait_for_target manually waits since cloud-init status --wait is insufficient:
# 1) `lxc exec` fails when vm isn't booted yet
# 2) `cloud-init status --wait` expects fs artifacts which don't exist when services
#    are overridden.
# 3) sometimes we need to wait when cloud-init is disabled
function wait_for_target(){
    # disable debug logging when waiting for target
    set +x
    local INSTANCE=$1
    local TARGET=$2
    local total=0
    while true; do
        # work around exec before dbus is available
        set +e
        lxc exec $INSTANCE -- systemctl is-active $TARGET.target &>/dev/null
        local rc=$?
        set -e
        if [ $rc = 1 ]; then
            echo "WAITING: dbus ${total}s"
        elif [ $rc = 3 ]; then
            echo "WAITING: dbus ${total}s"
        elif [ $rc = 255 ];then
            echo "WAITING: vm not booted yet ${total}s"
        elif lxc exec $INSTANCE -- systemctl is-system-running | grep running &> /dev/null; then
            echo "DONE"
            set -x
            return
        elif lxc exec $INSTANCE -- systemctl is-active $TARGET.target | grep active &> /dev/null; then
            # manually check if cloud-init.target is active yet
            echo "WAITING: almost booted, but not quite done"
        else
            echo "WAITING: inactive ${total}s"
        fi
        sleep 1
        total=$(( total + 1))
        if [[ $total -ge 150 ]]; then
            notify "ERROR: getting slow"
            lxc exec $INSTANCE -- sh -c "systemctl list-jobs"
        fi
    done
}

function gather(){
    set +x
    local INSTANCE=$1
    local OUT=$2
    echo "GATHERING"

    mkdir -p $OUT
    # TODO use image serial as file name via:
    #
    #    lxc list -f csv -c 'image.serial' oracular-container
    #
    # defaults to gathering for multi-user.target
    lxc exec $INSTANCE -- systemd-analyze dot > $OUT/dot-total.dot 2>/dev/null
    lxc exec $INSTANCE -- systemd-analyze dot --order > $OUT/dot-order.dot 2>/dev/null
    lxc exec $INSTANCE -- systemd-analyze dot --require > $OUT/dot-require.dot 2>/dev/null
    lxc exec $INSTANCE -- systemd-analyze critical-chain > $OUT/chain.txt
    lxc exec $INSTANCE -- systemd-analyze critical-chain --fuzz=60> $OUT/chain-fuzz.txt
    lxc exec $INSTANCE -- systemd-analyze > $OUT/analyze.txt
    lxc exec $INSTANCE -- systemd-analyze blame > $OUT/blame.txt
    lxc exec $INSTANCE -- systemd-analyze dump > $OUT/dump.txt
    set -x
}

function clean_rerun(){
    local INSTANCE="$1"
    local FLAVOR=$2
    local WAIT_TARGET=$3

    echo "LAUNCHING: instrumentation=$FLAVOR instance=$INSTANCE"
    lxc exec $INSTANCE -- cloud-init clean --machine-id --logs
    lxc stop $INSTANCE

    # gather cached data
    lxc start $INSTANCE
    wait_for_target $INSTANCE $WAIT_TARGET
    gather $INSTANCE "$OUT/$INSTANCE/$FLAVOR"
}

function backup_generator(){
    lxc exec $INSTANCE -- mv $GENERATOR $GENERATOR_BACKUP
}

function unbackup_generator(){
    lxc exec $INSTANCE -- mv $GENERATOR_BACKUP $GENERATOR
}

function install_file(){
    local TEMP_SERVICE=$(mktemp)
    local INSTANCE="$1"
    local FILE_NAME="$2"
    local CONTENT="$3"
    echo "$CONTENT" > $TEMP_SERVICE
    lxc file push $TEMP_SERVICE $INSTANCE/$LIB_SYSTEM/$FILE_NAME
}

function uninstall_file(){
    local INSTANCE="$1"
    local FILE_NAME="$2"
    lxc file delete $INSTANCE/$LIB_SYSTEM/$FILE_NAME
}

function service_template(){
    local ORDER="$1"
    cat << EOF
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
ExecStart=/$TAKO_INSTALLED_BINARY notify tako.d/systemd order=$ORDER

[Install]
WantedBy=tako.target
EOF
}

# unused
function push_file(){
    local INSTANCE="$1"
    local FILE="$2"
    local TMP_FILE="$3"
    lxc file push $TMP_FILE $INSTANCE/$FILE
}

#
# gather functions
#

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

function gather_modified_order_simplified_disabled(){
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
    local PERSISTENT_TEMP="/usr/local/temp"
    local INSTANCE="$1"
    local OVERRIDE_AFTER=$(mktemp)
    local OVERRIDE_BEFORE=$(mktemp)
    local OVERRIDE_MAIN=$(mktemp)
    #
    # setup
    #
    # remove generator
    backup_generator

    # install new services
    install_file $INSTANCE $TAKO.service "$TAKO_DAEMON_SERVICE_CONTENT"
    install_file $INSTANCE tako.target "$TAKO_TARGET_CONTENT"

    # enable targets
    lxc exec $INSTANCE -- systemctl daemon-reload
    lxc exec $INSTANCE -- systemctl enable tako.target

    # no [Install] section, so fry an egg with a magnifying glass
    lxc exec $INSTANCE -- ln -s /lib/systemd/system/cloud-init.target /etc/systemd/system/multi-user.target.wants/cloud-init.target

    # install tako binary
    lxc file push $TAKO_LOCAL_BINARY $INSTANCE/$TAKO_INSTALLED_PATH/

    # backup services
    lxc exec $INSTANCE -- mkdir -p $PERSISTENT_TEMP
    lxc exec $INSTANCE -- mv /$LIB_SYSTEM/cloud-init-local.service /$PERSISTENT_TEMP/cloud-init-local.service
    lxc exec $INSTANCE -- mv /$LIB_SYSTEM/cloud-init-network.service /$PERSISTENT_TEMP/cloud-init-network.service
    lxc exec $INSTANCE -- mv /$LIB_SYSTEM/cloud-config.service /$PERSISTENT_TEMP/cloud-config.service
    lxc exec $INSTANCE -- mv /$LIB_SYSTEM/cloud-final.service /$PERSISTENT_TEMP/cloud-final.service

    # install services
    install_file $INSTANCE cloud-init-local.service "$CLOUD_INIT_LOCAL_CONTENT"
    install_file $INSTANCE cloud-init-local-after.service "$CLOUD_INIT_LOCAL_AFTER_CONTENT"
    install_file $INSTANCE cloud-init-network.service "$CLOUD_INIT_NETWORK_CONTENT"
    install_file $INSTANCE cloud-init-network-after.service "$CLOUD_INIT_NETWORK_AFTER_CONTENT"
    install_file $INSTANCE cloud-config.service "$CLOUD_INIT_CONFIG_CONTENT"
    install_file $INSTANCE cloud-config-after.service "$CLOUD_INIT_CONFIG_AFTER_CONTENT"
    install_file $INSTANCE cloud-final.service "$CLOUD_INIT_FINAL_CONTENT"
    install_file $INSTANCE cloud-final-after.service "$CLOUD_INIT_FINAL_AFTER_CONTENT"

    # override main
    lxc exec $INSTANCE -- mkdir -p /$LIB_SYSTEM/$MAIN_D
    printf '[Service]\nExecStart=\nExecStart=systemd-notify --ready --status "done"\n' > $OVERRIDE_MAIN
    lxc file push $OVERRIDE_MAIN $INSTANCE/$LIB_SYSTEM/$MAIN_D/override.conf

    # re-run: no-op
    clean_rerun $INSTANCE "modified-order-simplified-disabled" $GRAPHICAL

    # teardown
    # for DIR in $LOCAL_D $NETWORK_D $CONFIG_D $FINAL_D; do
    #     lxc exec $INSTANCE -- rm -rf /$ETC_SYSTEM/$DIR
    # done
    # for DIR in $LOCAL_AFTER_D $NETWORK_AFTER_D $CONFIG_AFTER_D $FINAL_AFTER_D; do
    #     lxc exec $INSTANCE -- rm -rf /$ETC_SYSTEM/$DIR
    # done

    # reinstate originals
    lxc exec $INSTANCE -- mv /$PERSISTENT_TEMP/cloud-init-local.service /$LIB_SYSTEM/cloud-init-local.service
    lxc exec $INSTANCE -- mv /$PERSISTENT_TEMP/cloud-init-network.service /$LIB_SYSTEM/cloud-init-network.service
    lxc exec $INSTANCE -- mv /$PERSISTENT_TEMP/cloud-config.service /$LIB_SYSTEM/cloud-config.service
    lxc exec $INSTANCE -- mv /$PERSISTENT_TEMP/cloud-final.service /$LIB_SYSTEM/cloud-final.service

    # remove unwanted
    lxc exec $INSTANCE -- rm /$LIB_SYSTEM/cloud-init-local-after.service
    lxc exec $INSTANCE -- rm /$LIB_SYSTEM/cloud-init-network-after.service
    lxc exec $INSTANCE -- rm /$LIB_SYSTEM/cloud-config-after.service
    lxc exec $INSTANCE -- rm /$LIB_SYSTEM/cloud-final-after.service

    # undo generator
    unbackup_generator

    # enable targets
    lxc exec $INSTANCE -- systemctl disable tako.target

    # no [Install] section, so fry an egg with a magnifying glass
    lxc exec $INSTANCE -- rm /etc/systemd/system/multi-user.target.wants/cloud-init.target

    # install tako binary
    lxc file delete $INSTANCE/$TAKO_INSTALLED_BINARY

    # uninstall new services
    uninstall_file $INSTANCE $TAKO.service
    uninstall_file $INSTANCE tako.target

    lxc exec $INSTANCE -- systemctl daemon-reload

}

function gather_modified_order_simplified_no_op_disabled(){
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
    # remove generator
    backup_generator

    # no [Install] section, so fry an egg with a magnifying glass
    lxc exec $INSTANCE -- ln -s /lib/systemd/system/cloud-init.target /etc/systemd/system/multi-user.target.wants/cloud-init.target

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
    clean_rerun $INSTANCE "modified-order-simplified-no-op-disabled" $GRAPHICAL

    # teardown
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
    lxc exec $INSTANCE -- rm -rf /$LIB_SYSTEM/$MAIN_D

    # no [Install] section, so fry an egg with a magnifying glass
    lxc exec $INSTANCE -- rm -f /etc/systemd/system/multi-user.target.wants/cloud-init.target

    # undo generator
    unbackup_generator
}

# NOTE: to check for notices run:
#
#     TAKO=/run/tako/ tako notices --key=tako.d/systemd
function gather_modified_order_generalized(){
    local LIB_SYSTEM=lib/systemd/system
    local INSTANCE="$1"
    local OVERRIDE_AFTER=$(mktemp)
    local OVERRIDE_BEFORE=$(mktemp)
    local OVERRIDE_MAIN=$(mktemp)
    local PERSISTENT_TEMP="/usr/local/temp"

    # setup
    #
    # remove generator
    backup_generator

    # remove all services
    lxc exec $INSTANCE -- mkdir -p $PERSISTENT_TEMP
    lxc exec $INSTANCE -- mv /$LIB_SYSTEM/cloud-init-main.service $PERSISTENT_TEMP
    lxc exec $INSTANCE -- mv /$LIB_SYSTEM/cloud-init-local.service $PERSISTENT_TEMP
    lxc exec $INSTANCE -- mv /$LIB_SYSTEM/cloud-init-network.service $PERSISTENT_TEMP
    lxc exec $INSTANCE -- mv /$LIB_SYSTEM/cloud-config.service $PERSISTENT_TEMP
    lxc exec $INSTANCE -- mv /$LIB_SYSTEM/cloud-final.service $PERSISTENT_TEMP

    # install new services
    install_file $INSTANCE $TAKO.service "$TAKO_DAEMON_SERVICE_CONTENT"
    install_file $INSTANCE tako.target "$TAKO_TARGET_CONTENT"

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

    # enable targets
    lxc exec $INSTANCE -- systemctl daemon-reload
    lxc exec $INSTANCE -- systemctl enable tako.target

    # no [Install] section, so fry an egg with a magnifying glass
    lxc exec $INSTANCE -- ln -s /lib/systemd/system/cloud-init.target /etc/systemd/system/multi-user.target.wants/cloud-init.target

    # install tako binary
    lxc file push $TAKO_LOCAL_BINARY $INSTANCE/$TAKO_INSTALLED_PATH/

    # re-run: no-op
    clean_rerun $INSTANCE "modified-order-generalized-disabled" $GRAPHICAL

    # teardown
    unbackup_generator

    # readadd all cloud-init services
    lxc exec $INSTANCE -- mv $PERSISTENT_TEMP/cloud-init-main.service /$LIB_SYSTEM
    lxc exec $INSTANCE -- mv $PERSISTENT_TEMP/cloud-init-local.service /$LIB_SYSTEM
    lxc exec $INSTANCE -- mv $PERSISTENT_TEMP/cloud-init-network.service /$LIB_SYSTEM
    lxc exec $INSTANCE -- mv $PERSISTENT_TEMP/cloud-config.service /$LIB_SYSTEM
    lxc exec $INSTANCE -- mv $PERSISTENT_TEMP/cloud-final.service /$LIB_SYSTEM
    lxc exec $INSTANCE -- rm -rf $PERSISTENT_TEMP

    # uninstall files
    #
    # disable targets
    lxc exec $INSTANCE -- systemctl disable tako.target

    # delete target
    lxc exec $INSTANCE -- rm -f /etc/systemd/system/multi-user.target.wants/cloud-init.target

    # install tako binary
    lxc file delete $INSTANCE/$TAKO_INSTALLED_BINARY
    uninstall_file $INSTANCE $TAKO.service
    uninstall_file $INSTANCE tako.target

    # strategy: ignore cloud-init services and duplicates
    #
    # client wrappers: local
    #
    # Wants=network-pre.target
    # After=hv_kvp_daemon.service
    # Before=network-pre.target
    # Before=shutdown.target
    # Before=sysinit.target
    uninstall_file $INSTANCE tako-local-after-hv.service
    uninstall_file $INSTANCE tako-local-before-sysinit.service
    uninstall_file $INSTANCE tako-local-before-network-pre.service
    uninstall_file $INSTANCE tako-local-before-shutdown.service

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
    uninstall_file $INSTANCE tako-network-after-networkd-wait-online.service
    uninstall_file $INSTANCE tako-network-after-networking.service
    uninstall_file $INSTANCE tako-network-before-network-online.service
    uninstall_file $INSTANCE tako-network-before-sshd.service
    uninstall_file $INSTANCE tako-network-before-user-sessions.service

    uninstall_file $INSTANCE tako-network-before-sshd-keygen.service

    #
    # client wrappers: config
    #
    # After=network-online.target
    uninstall_file $INSTANCE tako-config-after-network-online.service

    #
    # client wrappers: final
    #
    # After=network-online.target  # duplicate
    # After=time-sync.target
    # After=rc-local.service
    # After=multi-user.target
    # Before=apt-daily.service
    uninstall_file $INSTANCE tako-final-after-time-sync.target
    uninstall_file $INSTANCE tako-final-after-rc-local.service
    uninstall_file $INSTANCE tako-final-after-multi-user.target
    uninstall_file $INSTANCE tako-final-before-apt-daily.service
    lxc exec $INSTANCE -- systemctl daemon-reload
}

function gather_modified_order_simplified_enabled(){
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
    # remove generator
    backup_generator

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
    clean_rerun $INSTANCE "modified-order-simplified-enabled" $CLOUD_INIT
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

    # undo override main
    lxc exec $INSTANCE -- rm -rf /$LIB_SYSTEM/$MAIN_D

    # undo generator
    unbackup_generator
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

function gather_disabled_no_generator(){
    local INSTANCE="$1"

    # setup
    lxc exec $INSTANCE -- touch /etc/cloud/cloud-init.disabled
    backup_generator

    # re-run: disabled
    clean_rerun $INSTANCE "disabled-no-generator" $GRAPHICAL

    # teardown
    lxc exec $INSTANCE -- rm /etc/cloud/cloud-init.disabled
    unbackup_generator
}

function gather_generator_no_op(){
    local INSTANCE="$1"
    # setup
    backup_generator

    # re-run: generator no-op
    clean_rerun $INSTANCE "generator-no-op" $GRAPHICAL

    # teardown
    unbackup_generator
}

function gather_daemon_reload(){
    local INSTANCE="$1"

    # setup
    backup_generator
    # no [Install] section, so fry an egg with a magnifying glass
    lxc exec $INSTANCE -- ln -s /lib/systemd/system/cloud-init.target /etc/systemd/system/multi-user.target.wants/cloud-init.target
    install_file $INSTANCE cloud-init-local.service "$DAEMON_RELOAD_CONTENT"

    # re-run: generator no-op
    clean_rerun $INSTANCE "daemon-reload" $CLOUD_INIT

    # teardown
    lxc exec $INSTANCE -- rm -f /etc/systemd/system/multi-user.target.wants/cloud-init.target
    unbackup_generator
    uninstall_file $INSTANCE cloud-init-local.service
}

function run_test(){
    # launch once, to avoid differences due to caching effects (i.e. snapd)
    local INSTANCE="$1"
    local COMMAND="$2"
    local OUT="$3"

    gather_first_boot $INSTANCE "$COMMAND"


    # the following two measure enabled and are not as generally useful
    # gather_modified_order_simplified_enabled $INSTANCE
    # gather_cached $INSTANCE

    # different disabled strategies
    #gather_disabled_no_generator $INSTANCE
    #gather_generator_no_op $INSTANCE
    #gather_modified_order_simplified_no_op_disabled $INSTANCE
    gather_daemon_reload $INSTANCE


    #gather_no_ops $INSTANCE
    #gather_disabled $INSTANCE
    #gather_modified_order_simplified_disabled $INSTANCE
    #gather_modified_order_generalized $INSTANCE

    lxc rm -f $INSTANCE
}

function main(){
    # seeking statistical significance
    for ITER in $(seq 0 30); do
        mkdir -p $RESULTS/$ITER
        echo "ITER: $ITER"
        for TYPE in container; do
            INSTANCE="$SERIES-$TYPE"
            if [[ $TYPE == "vm" ]]; then
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
