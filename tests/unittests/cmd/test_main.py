# This file is part of cloud-init. See LICENSE file for license information.

import contextlib
import copy
import io
import logging
import os
from collections import namedtuple
from io import StringIO
from unittest import mock

import pytest

from cloudinit import helpers, safeyaml
from cloudinit.cmd import main
from cloudinit.util import ensure_dir, load_text_file, write_file
from tests.unittests.helpers import (
    FilesystemMockingTestCase,
    populate_dir,
    wrap_and_call,
)

M_PATH = "cloudinit.cmd.main."
Tmpdir = namedtuple("Tmpdir", ["tmpdir", "link_d", "data_d"])
MyArgs = namedtuple("MyArgs", "debug files force local reporter subcommand")


@pytest.fixture()
def mock_get_user_data_file(mocker, tmpdir):
    yield mocker.patch(
        "cloudinit.cmd.devel.logs._get_user_data_file",
        return_value=tmpdir.join("cloud"),
    )


@pytest.fixture(autouse=True, scope="module")
def disable_setup_logging():
    # setup_basic_logging can change the logging level to WARNING, so
    # ensure it is always mocked
    with mock.patch(f"{M_PATH}log.setup_basic_logging", autospec=True):
        yield


@pytest.fixture()
def mock_status_wrapper(mocker, tmpdir):
    link_d = os.path.join(tmpdir, "link")
    data_d = os.path.join(tmpdir, "data")
    with mocker.patch(
        "cloudinit.cmd.main.read_cfg_paths",
        return_value=mock.Mock(get_cpath=lambda _: data_d),
    ), mocker.patch(
        "cloudinit.cmd.main.os.path.normpath", return_value=link_d
    ):
        yield Tmpdir(tmpdir, link_d, data_d)


class TestMain(FilesystemMockingTestCase):
    with_logs = True
    allowed_subp = False

    def setUp(self):
        super(TestMain, self).setUp()
        self.new_root = self.tmp_dir()
        self.cloud_dir = self.tmp_path("var/lib/cloud/", dir=self.new_root)
        os.makedirs(self.cloud_dir)
        self.replicateTestRoot("simple_ubuntu", self.new_root)
        self.cfg = {
            "datasource_list": ["None"],
            "runcmd": ["ls /etc"],  # test ALL_DISTROS
            "system_info": {
                "paths": {
                    "cloud_dir": self.cloud_dir,
                    "run_dir": self.new_root,
                }
            },
            "write_files": [
                {
                    "path": "/etc/blah.ini",
                    "content": "blah",
                    "permissions": 0o755,
                },
            ],
            "cloud_init_modules": ["write_files", "runcmd"],
        }
        cloud_cfg = safeyaml.dumps(self.cfg)
        ensure_dir(os.path.join(self.new_root, "etc", "cloud"))
        self.cloud_cfg_file = os.path.join(
            self.new_root, "etc", "cloud", "cloud.cfg"
        )
        write_file(self.cloud_cfg_file, cloud_cfg)
        self.patchOS(self.new_root)
        self.patchUtils(self.new_root)
        self.stderr = StringIO()
        self.patchStdoutAndStderr(stderr=self.stderr)
        # Every cc_ module calls get_meta_doc on import.
        # This call will fail if filesystem redirection mocks are in place
        # and the module hasn't already been imported which can depend
        # on test ordering.
        self.m_doc = mock.patch(
            "cloudinit.config.schema.get_meta_doc", return_value={}
        )
        self.m_doc.start()

    def tearDown(self):
        self.m_doc.stop()
        super().tearDown()

    def test_main_init_run_net_runs_modules(self):
        """Modules like write_files are run in 'net' mode."""
        cmdargs = MyArgs(
            debug=False,
            files=None,
            force=False,
            local=False,
            reporter=None,
            subcommand="init",
        )
        (_item1, item2) = wrap_and_call(
            "cloudinit.cmd.main",
            {
                "util.close_stdin": True,
                "netinfo.debug_info": "my net debug info",
                "util.fixup_output": ("outfmt", "errfmt"),
            },
            main.main_init,
            "init",
            cmdargs,
        )
        self.assertEqual([], item2)
        # Instancify is called
        instance_id_path = "var/lib/cloud/data/instance-id"
        self.assertEqual(
            "iid-datasource-none\n",
            os.path.join(
                load_text_file(os.path.join(self.new_root, instance_id_path))
            ),
        )
        # modules are run (including write_files)
        self.assertEqual(
            "blah", load_text_file(os.path.join(self.new_root, "etc/blah.ini"))
        )
        expected_logs = [
            "network config is disabled by fallback",  # apply_network_config
            "my net debug info",  # netinfo.debug_info
        ]
        for log in expected_logs:
            self.assertIn(log, self.stderr.getvalue())

    def test_main_init_run_net_calls_set_hostname_when_metadata_present(self):
        """When local-hostname metadata is present, call cc_set_hostname."""
        self.cfg["datasource"] = {
            "None": {"metadata": {"local-hostname": "md-hostname"}}
        }
        cloud_cfg = safeyaml.dumps(self.cfg)
        write_file(self.cloud_cfg_file, cloud_cfg)
        cmdargs = MyArgs(
            debug=False,
            files=None,
            force=False,
            local=False,
            reporter=None,
            subcommand="init",
        )

        def set_hostname(name, cfg, cloud, args):
            self.assertEqual("set_hostname", name)
            updated_cfg = copy.deepcopy(self.cfg)
            updated_cfg.update(
                {
                    "def_log_file": "/var/log/cloud-init.log",
                    "log_cfgs": [],
                    "syslog_fix_perms": [
                        "syslog:adm",
                        "root:adm",
                        "root:wheel",
                        "root:root",
                    ],
                    "vendor_data": {"enabled": True, "prefix": []},
                    "vendor_data2": {"enabled": True, "prefix": []},
                }
            )
            updated_cfg.pop("system_info")

            self.assertEqual(updated_cfg, cfg)
            self.assertIsNone(args)

        (_item1, item2) = wrap_and_call(
            "cloudinit.cmd.main",
            {
                "util.close_stdin": True,
                "netinfo.debug_info": "my net debug info",
                "cc_set_hostname.handle": {"side_effect": set_hostname},
                "util.fixup_output": ("outfmt", "errfmt"),
            },
            main.main_init,
            "init",
            cmdargs,
        )
        self.assertEqual([], item2)
        # Instancify is called
        instance_id_path = "var/lib/cloud/data/instance-id"
        self.assertEqual(
            "iid-datasource-none\n",
            os.path.join(
                load_text_file(os.path.join(self.new_root, instance_id_path))
            ),
        )
        # modules are run (including write_files)
        self.assertEqual(
            "blah", load_text_file(os.path.join(self.new_root, "etc/blah.ini"))
        )
        expected_logs = [
            "network config is disabled by fallback",  # apply_network_config
            "my net debug info",  # netinfo.debug_info
        ]
        for log in expected_logs:
            self.assertIn(log, self.stderr.getvalue())

    @mock.patch("cloudinit.cmd.clean.get_parser")
    @mock.patch("cloudinit.cmd.clean.handle_clean_args")
    @mock.patch("cloudinit.log.configure_root_logger")
    def test_main_sys_argv(
        self,
        _m_configure_root_logger,
        _m_handle_clean_args,
        m_clean_get_parser,
    ):
        with mock.patch("sys.argv", ["cloudinit", "--debug", "clean"]):
            main.main()
        m_clean_get_parser.assert_called_once()


class TestShouldBringUpInterfaces:
    @pytest.mark.parametrize(
        "cfg_disable,args_local,expected",
        [
            (True, True, False),
            (True, False, False),
            (False, True, False),
            (False, False, True),
        ],
    )
    def test_should_bring_up_interfaces(
        self, cfg_disable, args_local, expected
    ):
        init = mock.Mock()
        init.cfg = {"disable_network_activation": cfg_disable}

        args = mock.Mock()
        args.local = args_local

        result = main._should_bring_up_interfaces(init, args)
        assert result == expected


class TestCLI:
    def _call_main(self, sysv_args=None):
        if not sysv_args:
            sysv_args = ["cloud-init"]
        try:
            return main.main(sysv_args=sysv_args)
        except SystemExit as e:
            return e.code

    @pytest.mark.parametrize(
        "action,name,match",
        [
            pytest.param(
                "doesnotmatter",
                "init1",
                "^unknown name: init1$",
                id="invalid_name",
            ),
            pytest.param(
                "modules_name",
                "modules",
                "^Invalid cloud init mode specified 'modules-bogusmode'$",
                id="invalid_modes",
            ),
        ],
    )
    def test_status_wrapper_errors(
        self, action, name, match, caplog, mock_status_wrapper
    ):
        FakeArgs = namedtuple("FakeArgs", ["action", "local", "mode"])
        my_action = mock.Mock()

        myargs = FakeArgs((action, my_action), False, "bogusmode")
        with pytest.raises(ValueError, match=match):
            main.status_wrapper(name, myargs)
        assert [] == my_action.call_args_list

    @mock.patch("cloudinit.cmd.main.atomic_helper.write_json")
    def test_status_wrapper_init_local_writes_fresh_status_info(
        self,
        m_json,
        mock_status_wrapper,
    ):
        """When running in init-local mode, status_wrapper writes status.json.

        Old status and results artifacts are also removed.
        """
        data_d = mock_status_wrapper.data_d
        link_d = mock_status_wrapper.link_d
        # Write old artifacts which will be removed or updated.
        for _dir in data_d, link_d:
            populate_dir(
                str(_dir), {"status.json": "old", "result.json": "old"}
            )

        FakeArgs = namedtuple("FakeArgs", ["action", "local", "mode"])

        def myaction(name, args):
            # Return an error to watch status capture them
            return "SomeDatasource", ["an error"]

        myargs = FakeArgs(("ignored_name", myaction), True, "bogusmode")
        main.status_wrapper("init", myargs)
        # No errors reported in status
        status_v1 = m_json.call_args_list[1][0][1]["v1"]
        assert status_v1.keys() == {
            "datasource",
            "init-local",
            "init",
            "modules-config",
            "modules-final",
            "stage",
        }
        assert ["an error"] == status_v1["init-local"]["errors"]
        assert "SomeDatasource" == status_v1["datasource"]
        assert False is os.path.exists(
            data_d.join("result.json")
        ), "unexpected result.json found"
        assert False is os.path.exists(
            link_d.join("result.json")
        ), "unexpected result.json link found"

    @mock.patch("cloudinit.cmd.main.atomic_helper.write_json")
    def test_status_wrapper_init_local_honor_cloud_dir(
        self, m_json, mocker, mock_status_wrapper
    ):
        """When running in init-local mode, status_wrapper honors cloud_dir."""
        cloud_dir = mock_status_wrapper.tmpdir.join("cloud")
        paths = helpers.Paths({"cloud_dir": str(cloud_dir)})
        mocker.patch(M_PATH + "read_cfg_paths", return_value=paths)
        data_d = mock_status_wrapper.data_d
        link_d = mock_status_wrapper.link_d

        FakeArgs = namedtuple("FakeArgs", ["action", "local", "mode"])

        def myaction(name, args):
            # Return an error to watch status capture them
            return "SomeDatasource", ["an_error"]

        myargs = FakeArgs(("ignored_name", myaction), True, "bogusmode")
        main.status_wrapper("init", myargs)  # No explicit data_d

        # Access cloud_dir directly
        status_v1 = m_json.call_args_list[1][0][1]["v1"]
        assert ["an_error"] == status_v1["init-local"]["errors"]
        assert "SomeDatasource" == status_v1["datasource"]
        assert False is os.path.exists(
            data_d.join("result.json")
        ), "unexpected result.json found"
        assert False is os.path.exists(
            link_d.join("result.json")
        ), "unexpected result.json link found"

    def test_no_arguments_shows_usage(self, capsys):
        exit_code = self._call_main()
        _out, err = capsys.readouterr()
        assert "usage: cloud-init" in err
        assert 2 == exit_code

    def test_no_arguments_shows_error_message(self, capsys):
        exit_code = self._call_main()
        missing_subcommand_message = (
            "the following arguments are required: subcommand"
        )
        _out, err = capsys.readouterr()
        assert (
            missing_subcommand_message in err
        ), "Did not find error message for missing subcommand"
        assert 2 == exit_code

    def test_all_subcommands_represented_in_help(self, capsys):
        """All known subparsers are represented in the cloud-int help doc."""
        self._call_main()
        _out, err = capsys.readouterr()
        expected_subcommands = [
            "analyze",
            "clean",
            "devel",
            "features",
            "init",
            "modules",
            "single",
            "schema",
        ]
        for subcommand in expected_subcommands:
            assert subcommand in err

    @pytest.mark.parametrize(
        "subcommand,log_to_stderr,mocks",
        (
            ("init", False, [mock.patch("cloudinit.cmd.main.status_wrapper")]),
            (
                "modules",
                False,
                [mock.patch("cloudinit.cmd.main.status_wrapper")],
            ),
            (
                "schema",
                True,
                [
                    mock.patch(
                        "cloudinit.stages.Init._read_cfg", return_value={}
                    ),
                    mock.patch("cloudinit.config.schema.handle_schema_args"),
                ],
            ),
        ),
    )
    @mock.patch("cloudinit.cmd.main.log.setup_basic_logging")
    def test_subcommands_log_to_stderr_via_setup_basic_logging(
        self, setup_basic_logging, subcommand, log_to_stderr, mocks
    ):
        """setup_basic_logging is called for modules to use stderr

        Subcommands with exception of 'init'  and 'modules' use
        setup_basic_logging to direct logged errors to stderr.
        """
        with contextlib.ExitStack() as mockstack:
            for mymock in mocks:
                mockstack.enter_context(mymock)
            self._call_main(["cloud-init", subcommand])
        if log_to_stderr:
            setup_basic_logging.assert_called_once_with(logging.WARNING)
        else:
            setup_basic_logging.assert_not_called()

    @pytest.mark.parametrize("subcommand", ["init", "modules"])
    @mock.patch("cloudinit.cmd.main.status_wrapper")
    def test_modules_subcommand_parser(self, m_status_wrapper, subcommand):
        """The subcommand 'subcommand' calls status_wrapper passing modules."""
        self._call_main(["cloud-init", subcommand])
        (name, parseargs) = m_status_wrapper.call_args_list[0][0]
        assert subcommand == name
        assert subcommand == parseargs.subcommand
        assert subcommand == parseargs.action[0]
        assert f"main_{subcommand}" == parseargs.action[1].__name__

    @pytest.mark.parametrize(
        "subcommand",
        [
            "analyze",
            "clean",
            "collect-logs",
            "devel",
            "status",
            "schema",
        ],
    )
    @mock.patch("cloudinit.stages.Init._read_cfg", return_value={})
    def test_conditional_subcommands_from_entry_point_sys_argv(
        self,
        m_read_cfg,
        subcommand,
        capsys,
        mock_get_user_data_file,
        mock_status_wrapper,
    ):
        """Subcommands from entry-point are properly parsed from sys.argv."""
        expected_error = f"usage: cloud-init {subcommand}"
        # The cloud-init entrypoint calls main without passing sys_argv
        with mock.patch("sys.argv", ["cloud-init", subcommand, "-h"]):
            try:
                main.main()
            except SystemExit as e:
                assert 0 == e.code  # exit 2 on proper -h usage
        out, _err = capsys.readouterr()
        assert expected_error in out

    @pytest.mark.parametrize(
        "subcommand",
        [
            "clean",
            "collect-logs",
            "status",
        ],
    )
    def test_subcommand_parser(
        self, subcommand, mock_get_user_data_file, mock_status_wrapper
    ):
        """cloud-init `subcommand` calls its subparser."""
        # Provide -h param to `subcommand` to avoid having to mock behavior.
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self._call_main(["cloud-init", subcommand, "-h"])
        assert f"usage: cloud-init {subcommand}" in out.getvalue()

    @pytest.mark.parametrize(
        "args,expected_subcommands",
        [
            ([], ["schema"]),
            (["analyze"], ["blame", "show", "dump"]),
        ],
    )
    def test_subcommand_parser_multi_arg(
        self, args, expected_subcommands, capsys
    ):
        """The subcommand cloud-init schema calls the correct subparser."""
        self._call_main(["cloud-init"] + args)
        _out, err = capsys.readouterr()
        for subcommand in expected_subcommands:
            assert subcommand in err

    @mock.patch("cloudinit.stages.Init._read_cfg", return_value={})
    def test_wb_schema_subcommand_parser(self, m_read_cfg, capsys):
        """The subcommand cloud-init schema calls the correct subparser."""
        exit_code = self._call_main(["cloud-init", "schema"])
        _out, err = capsys.readouterr()
        assert 1 == exit_code
        # Known whitebox output from schema subcommand
        assert (
            "Error:\n"
            "Expected one of --config-file, --system or --docs arguments\n"
            in err
        )

    @pytest.mark.parametrize(
        "args,expected_doc_sections,is_error",
        [
            pytest.param(
                ["all"],
                [
                    "**Supported distros:** all",
                    "**Supported distros:** almalinux, alpine, azurelinux, "
                    "centos, cloudlinux, cos, debian, eurolinux, fedora, "
                    "freebsd, mariner, miraclelinux, openbsd, openeuler, "
                    "OpenCloudOS, openmandriva, opensuse, opensuse-microos, "
                    "opensuse-tumbleweed, opensuse-leap, photon, rhel, rocky, "
                    "sle_hpc, sle-micro, sles, TencentOS, ubuntu, virtuozzo",
                    " **resize_rootfs:** ",
                    "(``true``/``false``/``noblock``)",
                    "runcmd:\n             - [ ls, -l, / ]\n",
                ],
                False,
                id="all_spot_check",
            ),
            pytest.param(
                ["cc_runcmd"],
                ["\nRuncmd\n------\n\nRun arbitrary commands\n"],
                False,
                id="single_spot_check",
            ),
            pytest.param(
                [
                    "cc_runcmd",
                    "cc_resizefs",
                ],
                [
                    "\nRuncmd\n------\n\nRun arbitrary commands",
                    "\nResizefs\n--------\n\nResize filesystem",
                ],
                False,
                id="multiple_spot_check",
            ),
            pytest.param(
                ["garbage_value"],
                ["Invalid --docs value"],
                True,
                id="bad_arg_fails",
            ),
        ],
    )
    @mock.patch("cloudinit.stages.Init._read_cfg", return_value={})
    def test_wb_schema_subcommand(
        self, m_read_cfg, args, expected_doc_sections, is_error
    ):
        """Validate that doc content has correct values."""

        # Note: patchStdoutAndStderr() is convenient for reducing boilerplate,
        # but inspecting the code for debugging is not ideal
        # contextlib.redirect_stdout() provides similar behavior as a context
        # manager
        out_or_err = io.StringIO()
        redirecter = (
            contextlib.redirect_stderr
            if is_error
            else contextlib.redirect_stdout
        )
        with redirecter(out_or_err):
            self._call_main(["cloud-init", "schema", "--docs"] + args)
        out_or_err = out_or_err.getvalue()
        for expected in expected_doc_sections:
            assert expected in out_or_err

    @mock.patch("cloudinit.cmd.main.main_single")
    def test_single_subcommand(self, m_main_single):
        """The subcommand 'single' calls main_single with valid args."""
        self._call_main(["cloud-init", "single", "--name", "cc_ntp"])
        (name, parseargs) = m_main_single.call_args_list[0][0]
        assert "single" == name
        assert "single" == parseargs.subcommand
        assert "single" == parseargs.action[0]
        assert False is parseargs.debug
        assert False is parseargs.force
        assert None is parseargs.frequency
        assert "cc_ntp" == parseargs.name
        assert False is parseargs.report

    @mock.patch("cloudinit.cmd.main.main_features")
    def test_features_hook_subcommand(self, m_features):
        """The subcommand 'features' calls main_features with args."""
        self._call_main(["cloud-init", "features"])
        (name, parseargs) = m_features.call_args_list[0][0]
        assert "features" == name
        assert "features" == parseargs.subcommand
        assert "features" == parseargs.action[0]
        assert False is parseargs.debug
        assert False is parseargs.force
