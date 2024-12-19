# Copyright (C) 2012 Canonical Ltd.
# Copyright (C) 2012, 2013 Hewlett-Packard Development Company, L.P.
# Copyright (C) 2012 Yahoo! Inc.
#
# This file is part of cloud-init. See LICENSE file for license information.

import copy
import json
import logging
import os
import sys
from contextlib import suppress
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import (
    Any,
    Dict,
    Final,
    Iterable,
    List,
    Literal,
    Optional,
    Set,
    Tuple,
    Union,
)

from cloudinit import (
    atomic_helper,
    cloud,
    distros,
    features,
    handlers,
    helpers,
    importer,
    lifecycle,
    net,
    sources,
    type_utils,
    util,
)
from cloudinit.config import Netv1, Netv2
from cloudinit.event import EventScope, EventType, userdata_to_events

# Default handlers (used if not overridden)
from cloudinit.handlers.boot_hook import BootHookPartHandler
from cloudinit.handlers.cloud_config import CloudConfigPartHandler
from cloudinit.handlers.jinja_template import JinjaTemplatePartHandler
from cloudinit.handlers.shell_script import ShellScriptPartHandler
from cloudinit.handlers.shell_script_by_frequency import (
    ShellScriptByFreqPartHandler,
)
from cloudinit.net import cmdline
from cloudinit.reporting import events
from cloudinit.settings import (
    CLOUD_CONFIG,
    DEFAULT_RUN_DIR,
    PER_ALWAYS,
    PER_INSTANCE,
    PER_ONCE,
)
from cloudinit.sources import NetworkConfigSource

LOG = logging.getLogger(__name__)

NO_PREVIOUS_INSTANCE_ID = "NO_PREVIOUS_INSTANCE_ID"


COMBINED_CLOUD_CONFIG_DOC = (
    "Aggregated cloud-config created by merging merged_system_cfg"
    " (/etc/cloud/cloud.cfg and /etc/cloud/cloud.cfg.d), metadata,"
    " vendordata and userdata. The combined_cloud_config represents"
    " the aggregated desired configuration acted upon by cloud-init."
)


@dataclass
class Semaphore:
    semaphore: helpers.FileSemaphores
    args: Tuple[str, str]


# Cloud-init might first check instance-id before trusting the cache (check).
# Cloud-init may use a pre-existing cache without checking instance-id (trust).
# Cloud-init's default behavior may be overriden by user configuration.
#
# https://docs.cloud-init.io/en/latest/explanation/first_boot.html
class CacheMode(str, Enum):
    trust = "trust"
    check = "check"


# used in status.json and some log strings
UserLongName = Literal[
    "Local Stage",
    "Network Stage",
    "Config Stage",
    "Final Stage",
]

# used in status.json and some log strings
UserShortName = Literal[
    "init-local",
    "init",
    "modules-config",
    "modules-final",
]

# The first argument in main_init() / main_modules() / main_single()
ArgName = Literal["init", "modules", "single"]

# Previously used internally for flow control but also gets logged sometimes
InternalName = Literal[
    "local",
    "network",
    "config",
    "final",
]
OtherName = Literal["single", "other"]

# Because code reuse is evil and user confusion is the goal.
#
# Kidding. Lets please standardize these strings. We really shouldn't need
# variants of a "short name" in our logs.
WelcomeName = Literal[
    "init-local", "init", "modules:config", "modules:final", "single"
]

ReportName = Literal[
    "init-local", "init-network", "modules-config", "modules-final", "single"
]


@dataclass
class BaseStage:
    """BaseStage stores attributes shared by BootStage and OtherStage

    deps: a list of required deps
    description: a free-form string describing the stage
    """

    deps: List[sources.Deps]
    description: str


@dataclass
class BootStage(BaseStage):
    """BootStage represents one of the four boot stages

    BootStage instances are used to control flow of code execution.
    BootStage instances contain non-changing values associated with the stage.

    welcome: the stage welcome log string
    name: the stage argparse "name"
    report: the stage name used in Report events
    long: user-facing stage name, capitalized
    status: status.json stage name, also used in some logs
    internal: a string that was previously used internally for flow control but
              also gets logged sometimes
    """

    welcome: WelcomeName
    name: ArgName
    report: ReportName
    long: UserLongName
    status: UserShortName
    internal: InternalName


@dataclass
class OtherStage(BaseStage):
    """OtherStage provides a Stage implementation in non-boot codepaths.

    welcome: the stage welcome log string
    report: the stage name used in Report events
    internal: a (mostly) not user-facing string
    """

    welcome: OtherName
    report: OtherName
    internal: OtherName


Stage = Union[OtherStage, BootStage]

# These module variables store static data that is associated with each stage.
# https://docs.cloud-init.io/en/latest/explanation/boot.html
#
# BootStage variables include: local, network, config, and final
local: Final = BootStage(
    internal="local",
    name="init",
    status="init-local",
    welcome="init-local",
    report="init-local",
    description="searching for network datasources",
    long="Local Stage",
    deps=[sources.DEP_FILESYSTEM],
)
network: Final = BootStage(
    internal="network",
    name="init",
    status="init",
    welcome="init",
    report="init-network",
    # this description is untruthful, modules run here too
    description="searching for network datasources",
    long="Network Stage",
    deps=[sources.DEP_FILESYSTEM, sources.DEP_NETWORK],
)
config: Final = BootStage(
    internal="config",
    name="modules",
    status="modules-config",
    welcome="modules:config",
    report="modules-config",
    description="running modules for config",
    long="Config Stage",
    deps=[],
)
final: Final = BootStage(
    internal="final",
    name="modules",
    status="modules-final",
    welcome="modules:final",
    report="modules-final",
    description="running modules for final",
    long="Final Stage",
    deps=[],
)
# OtherStage objects are used when Init() is used in non-boot codepaths.
#
# single is used as a Stage object in main_single()
single: Final = OtherStage(
    internal="single",
    welcome="single",
    report="single",
    description="running single stage",
    deps=[],
)
# other is used as a Stage object in all other codepaths
other: Final = OtherStage(
    internal="other",
    welcome="other",
    report="other",
    description="non-boot stage",
    deps=[],
)


def update_event_enabled(
    datasource: sources.DataSource,
    cfg: dict,
    event_source_type: EventType,
    scope: EventScope,
) -> bool:
    """Determine if a particular EventType is enabled.

    For the `event_source_type` passed in, check whether this EventType
    is enabled in the `updates` section of the userdata. If `updates`
    is not enabled in userdata, check if defined as one of the
    `default_events` on the datasource. `scope` may be used to
    narrow the check to a particular `EventScope`.

    Note that on first boot, userdata may NOT be available yet. In this
    case, we only have the data source's `default_update_events`,
    so an event that should be enabled in userdata may be denied.
    """
    default_events: Dict[EventScope, Set[EventType]] = (
        datasource.default_update_events
    )
    user_events: Dict[EventScope, Set[EventType]] = userdata_to_events(
        cfg.get("updates", {})
    )
    # A value in the first will override a value in the second
    allowed = util.mergemanydict(
        [
            copy.deepcopy(user_events),
            copy.deepcopy(default_events),
        ]
    )

    # Add supplemental hotplug event if supported and present in
    # hotplug.enabled file
    if EventType.HOTPLUG in datasource.supported_update_events.get(
        scope, set()
    ):
        hotplug_enabled_file = util.read_hotplug_enabled_file(datasource.paths)
        if scope.value in hotplug_enabled_file["scopes"]:
            LOG.debug(
                "Adding event: scope=%s EventType=%s found in %s",
                scope,
                EventType.HOTPLUG,
                datasource.paths.get_cpath("hotplug.enabled"),
            )
            if not allowed.get(scope):
                allowed[scope] = set()
            allowed[scope].add(EventType.HOTPLUG)

    LOG.debug("Allowed events: %s", allowed)

    scopes: Iterable[EventScope] = [scope]
    scope_values = [s.value for s in scopes]

    for evt_scope in scopes:
        if event_source_type in allowed.get(evt_scope, []):
            LOG.debug(
                "Event Allowed: scope=%s EventType=%s",
                evt_scope.value,
                event_source_type,
            )
            return True

    LOG.debug(
        "Event Denied: scopes=%s EventType=%s", scope_values, event_source_type
    )
    return False


class Init:
    def __init__(
        self,
        stage: Stage,
        reporter=None,
        cache_mode: Optional[CacheMode] = None,
    ):
        """the final boss

        This is a multi-purpose God object which should be exterminated.

        stage: which Stage Init() is currently running in
        reporter: a reporter object
        cache_mode: force a specific CacheMode
        """
        # Created on first use
        self._cfg: Dict[str, Any] = {}
        self._paths: Optional[helpers.Paths] = None
        self._distro: Optional[distros.Distro] = None
        self._datasource: Optional[sources.DataSource] = None

        # Changed only when a fetch occurs
        self.ds_restored = False
        self._previous_iid: Optional[str] = None

        # Arguments passed
        self.stage: Stage = stage
        self._cache_mode: Optional[CacheMode] = cache_mode
        if reporter is None:
            reporter = events.ReportEventStack(
                name="init-reporter",
                description="init-desc",
                reporting_enabled=False,
            )
        self.reporter = reporter

    def _reset(self):
        # Recreated on access
        self._cfg = {}
        self._paths = None
        self._distro = None

    @property
    def cache_mode(self) -> CacheMode:
        """cache_mode() defines whether to trust the cache"""

        # manually set
        if self._cache_mode:
            return self._cache_mode

        # inferred from system state
        self._cache_mode = CacheMode.trust
        if self.stage == local:
            if util.get_cfg_option_bool(self.cfg, "manual_cache_clean", False):
                LOG.debug("manual cache clean set from config")
                self._cache_mode = CacheMode.trust
            else:
                mfile = self.paths.get_ipath_cur("manual_clean_marker")
                if os.path.exists(mfile):
                    LOG.debug(
                        "manual cache clean found from marker: %s", mfile
                    )
                    self._cache_mode = CacheMode.trust
        return self._cache_mode

    @property
    def distro(self):
        if not self._distro:
            # Try to find the right class to use
            system_config = self._extract_cfg("system")
            distro_name = system_config.pop("distro", "ubuntu")
            distro_cls = distros.fetch(distro_name)
            LOG.debug("Using distro class %s", distro_cls)
            self._distro = distro_cls(distro_name, system_config, self.paths)
            # If we have an active datasource we need to adjust
            # said datasource and move its distro/system config
            # from whatever it was to a new set...
            if self._datasource is not None:
                self.datasource.distro = self._distro
                self.datasource.sys_cfg = self.cfg
        return self._distro

    @property
    def cfg(self):
        return self._extract_cfg("restricted")

    def _extract_cfg(self, restriction):
        # Ensure actually read
        self.read_cfg()
        # Nobody gets the real config
        ocfg = copy.deepcopy(self._cfg)
        if restriction == "restricted":
            ocfg.pop("system_info", None)
        elif restriction == "system":
            ocfg = util.get_cfg_by_path(ocfg, ("system_info",), {})
        elif restriction == "paths":
            ocfg = util.get_cfg_by_path(ocfg, ("system_info", "paths"), {})
        return ocfg

    @property
    def paths(self):
        if self._paths and self._paths.datasource:
            return self._paths
        self._paths = helpers.Paths(
            self._extract_cfg("paths"), self._datasource
        )
        return self._paths

    def _initial_subdirs(self):
        c_dir = self.paths.cloud_dir
        run_dir = self.paths.run_dir
        initial_dirs = [
            c_dir,
            os.path.join(c_dir, "scripts"),
            os.path.join(c_dir, "scripts", "per-instance"),
            os.path.join(c_dir, "scripts", "per-once"),
            os.path.join(c_dir, "scripts", "per-boot"),
            os.path.join(c_dir, "scripts", "vendor"),
            os.path.join(c_dir, "seed"),
            os.path.join(c_dir, "instances"),
            os.path.join(c_dir, "handlers"),
            os.path.join(c_dir, "sem"),
            os.path.join(c_dir, "data"),
            os.path.join(run_dir, "sem"),
        ]
        return initial_dirs

    def start_boot(self):
        """start_boot() removes old boot_finished file"""
        util.del_file(self.paths.boot_finished)

    def reset_instance(self):
        """reset_instance() removes the link to the instance cache"""
        util.del_file(self.paths.instance_link)

    def track_python_version_change(self):
        """Purge the cache if python version changed since last boot.

        There could be changes not represented in our cache (obj.pkl) after we
        upgrade to a new version of python, so at that point clear the cache
        """
        current_python_version = "%d.%d" % (
            sys.version_info.major,
            sys.version_info.minor,
        )
        python_version_path = os.path.join(
            self.paths.get_cpath("data"), "python-version"
        )
        if os.path.exists(python_version_path):
            # The Python version has changed out from under us, anything that
            # was pickled previously is likely useless due to API changes.
            if current_python_version != util.load_text_file(
                python_version_path
            ):
                LOG.debug("Python version change detected. Purging cache")
                self.reset_instance()
                util.write_file(python_version_path, current_python_version)
        else:
            if os.path.exists(self.paths.get_ipath_cur("obj_pkl")):
                LOG.info(
                    "Writing python-version file. "
                    "Cache compatibility status is currently unknown."
                )
            util.write_file(python_version_path, current_python_version)

    @staticmethod
    def _get_strictest_mode(mode_1: int, mode_2: int) -> int:
        return mode_1 & mode_2

    def initialize_filesystem(self):
        mode = 0o640

        util.ensure_dirs(self._initial_subdirs())
        log_file = util.get_cfg_option_str(self.cfg, "def_log_file")
        if log_file:
            # At this point the log file should have already been created
            # in the setupLogging function of log.py
            with suppress(OSError):
                mode = self._get_strictest_mode(
                    0o640, util.get_permissions(log_file)
                )

            # set file mode to the strictest of 0o640 and the current mode
            util.ensure_file(log_file, mode, preserve_mode=False)
            perms = self.cfg.get("syslog_fix_perms")
            if not perms:
                perms = {}
            if not isinstance(perms, list):
                perms = [perms]

            error = None
            for perm in perms:
                u, g = util.extract_usergroup(perm)
                try:
                    util.chownbyname(log_file, u, g)
                    return
                except OSError as e:
                    error = e

            LOG.warning(
                "Failed changing perms on '%s'. tried: %s. %s",
                log_file,
                ",".join(perms),
                error,
            )

    def read_cfg(self, extra_fns=None):
        if not self._cfg:
            self._cfg = self._read_cfg(extra_fns)

    def _read_cfg(self, extra_fns):
        """read and merge our configuration"""
        # No config is passed to Paths() here because we don't yet have a
        # config to pass. We must bootstrap a config to identify
        # distro-specific run_dir locations. Once we have the run_dir
        # we re-read our config with a valid Paths() object. This code has to
        # assume the location of /etc/cloud/cloud.cfg && /etc/cloud/cloud.cfg.d

        initial_config = self._read_bootstrap_cfg(extra_fns, {})
        paths = initial_config.get("system_info", {}).get("paths", {})

        # run_dir hasn't changed so we can safely return the config
        if paths.get("run_dir") in (DEFAULT_RUN_DIR, None):
            return initial_config

        # run_dir has changed so re-read the config to get a valid one
        # using the new location of run_dir
        return self._read_bootstrap_cfg(extra_fns, paths)

    def _read_bootstrap_cfg(self, extra_fns, bootstrapped_config: dict):
        no_cfg_paths = helpers.Paths(bootstrapped_config, self._datasource)
        instance_data_file = no_cfg_paths.get_runpath(
            "instance_data_sensitive"
        )
        merger = helpers.ConfigMerger(
            paths=no_cfg_paths,
            datasource=self._datasource,
            additional_fns=extra_fns,
            base_cfg=fetch_base_config(
                no_cfg_paths.run_dir, instance_data_file=instance_data_file
            ),
        )
        return merger.cfg

    def _restore_from_cache(self):
        # We try to restore from a current link and static path
        # by using the instance link, if purge_cache was called
        # the file wont exist.
        return sources.pkl_load(self.paths.get_ipath_cur("obj_pkl"))

    def _write_to_cache(self):
        if not self._datasource:
            return False
        if util.get_cfg_option_bool(self.cfg, "manual_cache_clean", False):
            # The empty file in instance/ dir indicates manual cleaning,
            # and can be read by ds-identify.
            util.write_file(
                self.paths.get_ipath_cur("manual_clean_marker"),
                omode="w",
                content="",
            )
        return sources.pkl_store(
            self.datasource, self.paths.get_ipath_cur("obj_pkl")
        )

    def _get_datasources(self):
        # Any config provided???
        pkg_list = self.cfg.get("datasource_pkg_list") or []
        # Add the defaults at the end
        for n in ["", type_utils.obj_name(sources)]:
            if n not in pkg_list:
                pkg_list.append(n)
        cfg_list = self.cfg.get("datasource_list") or []
        return (cfg_list, pkg_list)

    def _restore_from_checked_cache(self):

        ds = self._restore_from_cache()
        if not ds:
            return (None, "no cache found")

        run_iid_fn = self.paths.get_runpath("instance_id")
        if os.path.exists(run_iid_fn):
            run_iid = util.load_text_file(run_iid_fn).strip()
        else:
            run_iid = None

        if run_iid == ds.get_instance_id():
            return (ds, "restored from cache with run check: %s" % ds)
        elif self.cache_mode == CacheMode.trust:
            return (ds, "restored from cache: %s" % ds)
        else:
            if hasattr(ds, "check_instance_id") and ds.check_instance_id(
                self.cfg
            ):
                return (ds, "restored from checked cache: %s" % ds)
            else:
                return (None, "cache invalid in datasource: %s" % ds)

    @property
    def datasource(self) -> sources.DataSource:
        if self._datasource:
            return self._datasource

        with events.ReportEventStack(
            name="check-cache",
            description="attempting to read from cache [%s]" % self.cache_mode,
            parent=self.reporter,
        ) as myrep:
            ds, desc = self._restore_from_checked_cache()
            myrep.description = desc
            self.ds_restored = bool(ds)
            LOG.debug(myrep.description)

        if not ds:
            try:
                cfg_list, pkg_list = self._get_datasources()
                # Deep copy so that user-data handlers can not modify
                # (which will affect user-data handlers down the line...)
                ds, dsname = sources.find_source(
                    self.cfg,
                    self.distro,
                    self.paths,
                    copy.deepcopy(self.stage.deps),
                    cfg_list,
                    pkg_list,
                    self.reporter,
                )
                self.reset_instance()
                LOG.info("Loaded datasource %s - %s", dsname, ds)
            except sources.DataSourceNotFoundException as e:
                if self.cache_mode != CacheMode.check:
                    raise e
                ds = self._restore_from_cache()
                if ds and ds.check_if_fallback_is_allowed():
                    LOG.info(
                        "Restored fallback datasource from checked cache: %s",
                        ds,
                    )
                else:
                    self.reset_instance()
                    raise e
        self._datasource = ds
        # Ensure we adjust our path members datasource
        # now that we have one (thus allowing ipath to be used)
        self._reset()
        return ds

    def _get_instance_subdirs(self):
        return ["handlers", "scripts", "sem"]

    def _get_ipath(self, subname=None):
        # Force a check to see if anything
        # actually comes back, if not
        # then a datasource has not been assigned...
        instance_dir = self.paths.get_ipath(subname)
        if not instance_dir:
            raise RuntimeError(
                "No instance directory is available."
                " Has a datasource been fetched??"
            )
        return instance_dir

    def _write_network_config_json(self, netcfg: Optional[dict]):
        """Create /var/lib/cloud/instance/network-config.json

        Only attempt once /var/lib/cloud/instance exists which is created
        by Init.instancify once a datasource is detected.
        """

        if not os.path.islink(self.paths.instance_link):
            # Datasource hasn't been detected yet, so we may not
            # have visibility to datasource applicable network-config
            return
        ncfg_instance_path = self.paths.get_ipath_cur("network_config")
        network_link = self.paths.get_runpath("network_config")
        if os.path.exists(ncfg_instance_path):
            # Compare and only write on delta of current network-config
            if netcfg != util.load_json(
                util.load_text_file(ncfg_instance_path)
            ):
                atomic_helper.write_json(
                    ncfg_instance_path, netcfg, mode=0o600
                )
        else:
            atomic_helper.write_json(ncfg_instance_path, netcfg, mode=0o600)
        if not os.path.islink(network_link):
            util.sym_link(ncfg_instance_path, network_link)

    def _reflect_cur_instance(self):
        # Remove the old symlink and attach a new one so
        # that further reads/writes connect into the right location
        idir = self._get_ipath()
        destination = Path(self.paths.instance_link).resolve().absolute()
        already_instancified = destination == Path(idir).absolute()
        if already_instancified:
            LOG.info("Instance link already exists, not recreating it.")
        else:
            self.reset_instance()
            util.sym_link(idir, self.paths.instance_link)

        # Ensures these dirs exist
        dir_list = []
        for d in self._get_instance_subdirs():
            dir_list.append(os.path.join(idir, d))
        util.ensure_dirs(dir_list)

        # Write out information on what is being used for the current instance
        # and what may have been used for a previous instance...
        dp = self.paths.get_cpath("data")

        # Write what the datasource was and is..
        ds = "%s: %s" % (type_utils.obj_name(self.datasource), self.datasource)
        previous_ds = None
        ds_fn = os.path.join(idir, "datasource")
        try:
            previous_ds = util.load_text_file(ds_fn).strip()
        except Exception:
            pass
        if not previous_ds:
            previous_ds = ds
        util.write_file(ds_fn, "%s\n" % ds)
        util.write_file(
            os.path.join(dp, "previous-datasource"), "%s\n" % (previous_ds)
        )

        # What the instance id was and is...
        iid = self.datasource.get_instance_id()
        iid_fn = os.path.join(dp, "instance-id")

        previous_iid = self.previous_iid()
        util.write_file(iid_fn, "%s\n" % iid)
        util.write_file(self.paths.get_runpath("instance_id"), "%s\n" % iid)
        util.write_file(
            os.path.join(dp, "previous-instance-id"), "%s\n" % (previous_iid)
        )

        self._write_to_cache()
        if already_instancified and previous_ds == ds:
            LOG.info(
                "Not re-loading configuration, instance "
                "id and datasource have not changed."
            )
            # Ensure needed components are regenerated
            # after change of instance which may cause
            # change of configuration
        else:
            self._reset()
        return iid

    def previous_iid(self):
        if self._previous_iid is not None:
            return self._previous_iid

        dp = self.paths.get_cpath("data")
        iid_fn = os.path.join(dp, "instance-id")
        try:
            self._previous_iid = util.load_text_file(iid_fn).strip()
        except Exception:
            self._previous_iid = NO_PREVIOUS_INSTANCE_ID

        LOG.debug("previous iid found to be %s", self._previous_iid)
        return self._previous_iid

    def is_new_instance(self):
        """Return true if this is a new instance.

        If datasource has already been initialized, this will return False,
        even on first boot.
        """
        previous = self.previous_iid()
        ret = (
            previous == NO_PREVIOUS_INSTANCE_ID
            or previous != self.datasource.get_instance_id()
        )
        return ret

    def fetch(self):
        """fetch() is an alias for Init.datasource

        optionally load datasource from cache, otherwise discover datasource
        """
        return self.datasource

    def instancify(self):
        return self._reflect_cur_instance()

    def cloudify(self):
        # Form the needed options to cloudify our members
        return cloud.Cloud(
            self.datasource,
            self.paths,
            self.cfg,
            self.distro,
            helpers.Runners(self.paths),
            reporter=self.reporter,
        )

    def update(self):
        self._store_rawdata(self.datasource.get_userdata_raw(), "userdata")
        self._store_processeddata(self.datasource.get_userdata(), "userdata")
        self._store_raw_vendordata(
            self.datasource.get_vendordata_raw(), "vendordata"
        )
        self._store_processeddata(
            self.datasource.get_vendordata(), "vendordata"
        )
        self._store_raw_vendordata(
            self.datasource.get_vendordata2_raw(), "vendordata2"
        )
        self._store_processeddata(
            self.datasource.get_vendordata2(), "vendordata2"
        )

    def setup_datasource(self):
        with events.ReportEventStack(
            "setup-datasource", "setting up datasource", parent=self.reporter
        ):
            if self.datasource is None:
                raise RuntimeError("Datasource is None, cannot setup.")
            self.datasource.setup(is_new_instance=self.is_new_instance())

    def activate_datasource(self):
        with events.ReportEventStack(
            "activate-datasource",
            "activating datasource",
            parent=self.reporter,
        ):
            if self.datasource is None:
                raise RuntimeError("Datasource is None, cannot activate.")
            self.datasource.activate(
                cfg=self.cfg, is_new_instance=self.is_new_instance()
            )
            self._write_to_cache()

    def _store_rawdata(self, data, datasource):
        # Raw data is bytes, not a string
        if data is None:
            data = b""
        util.write_file(self._get_ipath("%s_raw" % datasource), data, 0o600)

    def _store_raw_vendordata(self, data, datasource):
        # Only these data types
        if data is not None and type(data) not in [bytes, str, list]:
            raise TypeError(
                "vendordata_raw is unsupported type '%s'" % str(type(data))
            )
        # This data may be a list, convert it to a string if so
        if isinstance(data, list):
            data = atomic_helper.json_dumps(data)
        self._store_rawdata(data, datasource)

    def _store_processeddata(self, processed_data, datasource):
        # processed is a Mime message, so write as string.
        if processed_data is None:
            processed_data = ""
        util.write_file(
            self._get_ipath(datasource), str(processed_data), 0o600
        )

    def _default_handlers(self, opts=None) -> List[handlers.Handler]:
        if opts is None:
            opts = {}

        opts.update(
            {
                "paths": self.paths,
                "datasource": self.datasource,
            }
        )
        # TODO(harlowja) Hmmm, should we dynamically import these??
        cloudconfig_handler = CloudConfigPartHandler(**opts)
        shellscript_handler = ShellScriptPartHandler(**opts)
        boothook_handler = BootHookPartHandler(**opts)
        def_handlers = [
            cloudconfig_handler,
            shellscript_handler,
            ShellScriptByFreqPartHandler(PER_ALWAYS, **opts),
            ShellScriptByFreqPartHandler(PER_INSTANCE, **opts),
            ShellScriptByFreqPartHandler(PER_ONCE, **opts),
            boothook_handler,
            JinjaTemplatePartHandler(
                **opts,
                sub_handlers=[
                    cloudconfig_handler,
                    shellscript_handler,
                    boothook_handler,
                ],
            ),
        ]
        return def_handlers

    def _default_vendordata_handlers(self):
        return self._default_handlers(
            opts={
                "script_path": "vendor_scripts",
                "cloud_config_path": "vendor_cloud_config",
            }
        )

    def _default_vendordata2_handlers(self):
        return self._default_handlers(
            opts={
                "script_path": "vendor_scripts",
                "cloud_config_path": "vendor2_cloud_config",
            }
        )

    def _do_handlers(
        self, data_msg, c_handlers_list, frequency, excluded=None
    ):
        """
        Generalized handlers suitable for use with either vendordata
        or userdata
        """
        if excluded is None:
            excluded = []

        cdir = self.paths.get_cpath("handlers")
        idir = self._get_ipath("handlers")

        # Add the path to the plugins dir to the top of our list for importing
        # new handlers.
        #
        # Note(harlowja): instance dir should be read before cloud-dir
        for d in [cdir, idir]:
            if d and d not in sys.path:
                sys.path.insert(0, d)

        def register_handlers_in_dir(path):
            # Attempts to register any handler modules under the given path.
            if not path or not os.path.isdir(path):
                return
            potential_handlers = util.get_modules_from_dir(path)
            for fname, mod_name in potential_handlers.items():
                try:
                    mod_locs, looked_locs = importer.find_module(
                        mod_name, [""], ["list_types", "handle_part"]
                    )
                    if not mod_locs:
                        LOG.warning(
                            "Could not find a valid user-data handler"
                            " named %s in file %s (searched %s)",
                            mod_name,
                            fname,
                            looked_locs,
                        )
                        continue
                    mod = importer.import_module(mod_locs[0])
                    mod = handlers.fixup_handler(mod)
                    types = c_handlers.register(mod)
                    if types:
                        LOG.debug(
                            "Added custom handler for %s [%s] from %s",
                            types,
                            mod,
                            fname,
                        )
                except Exception:
                    util.logexc(
                        LOG, "Failed to register handler from %s", fname
                    )

        # This keeps track of all the active handlers
        c_handlers = helpers.ContentHandlers()

        # Add any handlers in the cloud-dir
        register_handlers_in_dir(cdir)

        # Register any other handlers that come from the default set. This
        # is done after the cloud-dir handlers so that the cdir modules can
        # take over the default user-data handler content-types.
        for mod in c_handlers_list:
            types = c_handlers.register(mod, overwrite=False)
            if types:
                LOG.debug("Added default handler for %s from %s", types, mod)

        # Form our cloud interface
        data = self.cloudify()

        def init_handlers():
            # Init the handlers first
            for _ctype, mod in c_handlers.items():
                if mod in c_handlers.initialized:
                    # Avoid initiating the same module twice (if said module
                    # is registered to more than one content-type).
                    continue
                handlers.call_begin(mod, data, frequency)
                c_handlers.initialized.append(mod)

        def walk_handlers(excluded):
            # Walk the user data
            part_data = {
                "handlers": c_handlers,
                # Any new handlers that are encountered get written here
                "handlerdir": idir,
                "data": data,
                # The default frequency if handlers don't have one
                "frequency": frequency,
                # This will be used when new handlers are found
                # to help write their contents to files with numbered
                # names...
                "handlercount": 0,
                "excluded": excluded,
            }
            handlers.walk(data_msg, handlers.walker_callback, data=part_data)

        def finalize_handlers():
            # Give callbacks opportunity to finalize
            for _ctype, mod in c_handlers.items():
                if mod not in c_handlers.initialized:
                    # Said module was never inited in the first place, so lets
                    # not attempt to finalize those that never got called.
                    continue
                c_handlers.initialized.remove(mod)
                try:
                    handlers.call_end(mod, data, frequency)
                except Exception:
                    util.logexc(LOG, "Failed to finalize handler: %s", mod)

        try:
            init_handlers()
            walk_handlers(excluded)
        finally:
            finalize_handlers()

    def consume_data(self, frequency=PER_INSTANCE):
        # Consume the userdata first, because we need want to let the part
        # handlers run first (for merging stuff)
        with events.ReportEventStack(
            "consume-user-data",
            "reading and applying user-data",
            parent=self.reporter,
        ):
            if util.get_cfg_option_bool(self.cfg, "allow_userdata", True):
                self._consume_userdata(frequency)
            else:
                LOG.debug("allow_userdata = False: discarding user-data")

        with events.ReportEventStack(
            "consume-vendor-data",
            "reading and applying vendor-data",
            parent=self.reporter,
        ):
            self._consume_vendordata("vendordata", frequency)

        with events.ReportEventStack(
            "consume-vendor-data2",
            "reading and applying vendor-data2",
            parent=self.reporter,
        ):
            self._consume_vendordata("vendordata2", frequency)

        # Perform post-consumption adjustments so that
        # modules that run during the init stage reflect
        # this consumed set.
        #
        # They will be recreated on future access...
        self._reset()
        # Note(harlowja): the 'active' datasource will have
        # references to the previous config, distro, paths
        # objects before the load of the userdata happened,
        # this is expected.
        combined_cloud_cfg = copy.deepcopy(self.cfg)
        combined_cloud_cfg["_doc"] = COMBINED_CLOUD_CONFIG_DOC
        # Persist system_info key from /etc/cloud/cloud.cfg in both
        # combined_cloud_config file and instance-data-sensitive.json's
        # merged_system_cfg key.
        combined_cloud_cfg["system_info"] = self._extract_cfg("system")
        # Add features information to allow for file-based discovery of
        # feature settings.
        combined_cloud_cfg["features"] = features.get_features()
        atomic_helper.write_json(
            self.paths.get_runpath("combined_cloud_config"),
            combined_cloud_cfg,
            mode=0o600,
        )
        json_sensitive_file = self.paths.get_runpath("instance_data_sensitive")
        try:
            instance_json = util.load_json(
                util.load_text_file(json_sensitive_file)
            )
        except (OSError, IOError) as e:
            LOG.warning(
                "Skipping write of system_info/features to %s."
                " Unable to read file: %s",
                json_sensitive_file,
                e,
            )
            return
        except (json.JSONDecodeError, TypeError) as e:
            LOG.warning(
                "Skipping write of system_info/features to %s."
                " Invalid JSON found: %s",
                json_sensitive_file,
                e,
            )
            return
        instance_json["system_info"] = combined_cloud_cfg["system_info"]
        instance_json["features"] = combined_cloud_cfg["features"]
        atomic_helper.write_json(
            json_sensitive_file,
            instance_json,
            mode=0o600,
        )

    def _consume_vendordata(self, vendor_source, frequency=PER_INSTANCE):
        """
        Consume the vendordata and run the part handlers on it
        """

        # User-data should have been consumed first.
        # So we merge the other available cloud-configs (everything except
        # vendor provided), and check whether or not we should consume
        # vendor data at all. That gives user or system a chance to override.
        if vendor_source == "vendordata":
            if not self.datasource.get_vendordata_raw():
                LOG.debug("no vendordata from datasource")
                return
            cfg_name = "vendor_data"
        elif vendor_source == "vendordata2":
            if not self.datasource.get_vendordata2_raw():
                LOG.debug("no vendordata2 from datasource")
                return
            cfg_name = "vendor_data2"
        else:
            raise RuntimeError(
                "vendor_source arg must be either 'vendordata'"
                " or 'vendordata2'"
            )

        _cc_merger = helpers.ConfigMerger(
            paths=self._paths,
            datasource=self.datasource,
            additional_fns=[],
            base_cfg=self.cfg,
            include_vendor=False,
        )
        vdcfg = _cc_merger.cfg.get(cfg_name, {})

        if not isinstance(vdcfg, dict):
            vdcfg = {"enabled": False}
            LOG.warning(
                "invalid %s setting. resetting to: %s", cfg_name, vdcfg
            )

        enabled = vdcfg.get("enabled")
        no_handlers = vdcfg.get("disabled_handlers", None)

        if not util.is_true(enabled):
            LOG.debug("%s consumption is disabled.", vendor_source)
            return

        if isinstance(enabled, str):
            lifecycle.deprecate(
                deprecated=f"Use of string '{enabled}' for "
                "'vendor_data:enabled' field",
                deprecated_version="23.1",
                extra_message="Use boolean value instead.",
            )

        LOG.debug(
            "%s will be consumed. disabled_handlers=%s",
            vendor_source,
            no_handlers,
        )

        # Ensure vendordata source fetched before activation (just in case.)

        # c_handlers_list keeps track of all the active handlers, while
        # excluding what the users doesn't want run, i.e. boot_hook,
        # cloud_config, shell_script
        if vendor_source == "vendordata":
            vendor_data_msg = self.datasource.get_vendordata()
            c_handlers_list = self._default_vendordata_handlers()
        else:
            vendor_data_msg = self.datasource.get_vendordata2()
            c_handlers_list = self._default_vendordata2_handlers()

        # Run the handlers
        self._do_handlers(
            vendor_data_msg, c_handlers_list, frequency, excluded=no_handlers
        )

    def _consume_userdata(self, frequency=PER_INSTANCE):
        """
        Consume the userdata and run the part handlers
        """

        # Ensure datasource fetched before activation (just incase)
        user_data_msg = self.datasource.get_userdata(True)

        # This keeps track of all the active handlers
        c_handlers_list = self._default_handlers()

        # Run the handlers
        self._do_handlers(user_data_msg, c_handlers_list, frequency)

    def _get_network_key_contents(self, cfg) -> Union[Netv1, Netv2, None]:
        """
        Network configuration can be passed as a dict under a "network" key, or
        optionally at the top level. In both cases, return the config.
        """
        if cfg and "network" in cfg:
            return cfg["network"]
        return cfg

    def _find_networking_config(
        self,
    ) -> Tuple[Union[Netv1, Netv2, None], Union[NetworkConfigSource, str]]:
        disable_file = os.path.join(
            self.paths.get_cpath("data"), "upgraded-network"
        )
        if os.path.exists(disable_file):
            return (None, disable_file)

        available_cfgs = {
            NetworkConfigSource.CMD_LINE: cmdline.read_kernel_cmdline_config(),
            NetworkConfigSource.INITRAMFS: cmdline.read_initramfs_config(),
            NetworkConfigSource.DS: None,
            NetworkConfigSource.SYSTEM_CFG: self.cfg.get("network"),
        }

        if self.datasource and hasattr(self.datasource, "network_config"):
            available_cfgs[NetworkConfigSource.DS] = (
                self.datasource.network_config
            )

        if self.datasource:
            order = self.datasource.network_config_sources
        else:
            order = sources.DataSource.network_config_sources
        for cfg_source in order:
            if not isinstance(cfg_source, NetworkConfigSource):
                # This won't happen in the cloud-init codebase, but out-of-tree
                # datasources might have an invalid type that mypy cannot know.
                LOG.warning(  # type: ignore
                    "data source specifies an invalid network cfg_source: %s",
                    cfg_source,
                )
                continue
            if cfg_source not in available_cfgs:
                LOG.warning(
                    "data source specifies an unavailable network"
                    " cfg_source: %s",
                    cfg_source,
                )
                continue
            ncfg = self._get_network_key_contents(available_cfgs[cfg_source])
            if net.is_disabled_cfg(ncfg):
                LOG.debug("network config disabled by %s", cfg_source)
                return (None, cfg_source)
            if ncfg:
                return (ncfg, cfg_source)
        if not self.cfg.get("network", True):
            LOG.warning("Empty network config found")
        return (
            self.distro.generate_fallback_config(),
            NetworkConfigSource.FALLBACK,
        )

    def _apply_netcfg_names(self, netcfg):
        try:
            LOG.debug("applying net config names for %s", netcfg)
            self.distro.networking.apply_network_config_names(netcfg)
        except Exception as e:
            LOG.warning("Failed to rename devices: %s", e)

    def _get_per_boot_network_semaphore(self):
        return Semaphore(
            semaphore=helpers.FileSemaphores(self.paths.get_runpath("sem")),
            args=("apply_network_config", PER_ONCE),
        )

    def _network_already_configured(self) -> bool:
        sem = self._get_per_boot_network_semaphore()
        return sem.semaphore.has_run(*sem.args)

    def apply_network_config(self, bring_up):
        """Apply the network config.

        Find the config, determine whether to apply it, apply it via
        the distro, and optionally bring it up
        """
        from cloudinit.config.schema import (
            SchemaType,
            validate_cloudconfig_schema,
        )

        netcfg, src = self._find_networking_config()
        if netcfg is None:
            LOG.info("network config is disabled by %s", src)
            return

        def event_enabled_and_metadata_updated(event_type):
            return update_event_enabled(
                datasource=self.datasource,
                cfg=self.cfg,
                event_source_type=event_type,
                scope=EventScope.NETWORK,
            ) and self.datasource.update_metadata_if_supported([event_type])

        def should_run_on_boot_event():
            return (
                not self._network_already_configured()
                and event_enabled_and_metadata_updated(EventType.BOOT)
            )

        if (
            self.datasource is not None
            and not self.is_new_instance()
            and not should_run_on_boot_event()
            and not event_enabled_and_metadata_updated(EventType.BOOT_LEGACY)
        ):
            LOG.debug(
                "No network config applied. Neither a new instance"
                " nor datasource network update allowed"
            )
            # nothing new, but ensure proper names
            self._apply_netcfg_names(netcfg)
            return

        # refresh netcfg after update
        netcfg, src = self._find_networking_config()
        self._write_network_config_json(netcfg)

        if netcfg:
            validate_cloudconfig_schema(
                config=netcfg,
                schema_type=SchemaType.NETWORK_CONFIG,
                strict=False,  # Warnings not raising exceptions
                log_details=False,  # May have wifi passwords in net cfg
                log_deprecations=True,
            )
        # ensure all physical devices in config are present
        self.distro.networking.wait_for_physdevs(netcfg)

        # apply renames from config
        self._apply_netcfg_names(netcfg)

        # rendering config
        LOG.info(
            "Applying network configuration from %s bringup=%s: %s",
            src,
            bring_up,
            netcfg,
        )

        sem = self._get_per_boot_network_semaphore()
        try:
            with sem.semaphore.lock(*sem.args):
                return self.distro.apply_network_config(
                    netcfg, bring_up=bring_up
                )
        except net.RendererNotFoundError as e:
            LOG.error(
                "Unable to render networking. Network config is "
                "likely broken: %s",
                e,
            )
            return
        except NotImplementedError:
            LOG.warning(
                "distro '%s' does not implement apply_network_config. "
                "networking may not be configured properly.",
                self.distro,
            )
            return


def read_runtime_config(run_dir: str):
    return util.read_conf(os.path.join(run_dir, "cloud.cfg"))


def fetch_base_config(run_dir: str, *, instance_data_file=None) -> dict:
    return util.mergemanydict(
        [
            # builtin config, hardcoded in settings.py.
            util.get_builtin_cfg(),
            # Anything in your conf.d or 'default' cloud.cfg location.
            util.read_conf_with_confd(
                CLOUD_CONFIG, instance_data_file=instance_data_file
            ),
            # runtime config. I.e., /run/cloud-init/cloud.cfg
            read_runtime_config(run_dir),
            # Kernel/cmdline parameters override system config
            util.read_conf_from_cmdline(),
        ],
        reverse=True,
    )
