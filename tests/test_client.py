import contextlib
import os
import platform
import shutil
import socket
import stat
import tempfile
import unittest
from pathlib import Path
from textwrap import dedent
from typing import Any, ContextManager, FrozenSet, List, Optional, Tuple

import pyspawner


def child_main(indented_code: str) -> None:
    code = dedent(indented_code)
    code_obj = compile(
        code, "<child_main string>", "exec", dont_inherit=True, optimize=0
    )
    # Exec in global scope, so imports go to globals, not locals
    exec(code_obj, globals(), globals())


@contextlib.contextmanager
def _spawned_child_context(
    client: pyspawner.Client,
    args: List[Any] = [],
    sandbox_config: pyspawner.SandboxConfig = pyspawner.SandboxConfig(),
) -> ContextManager[pyspawner.ChildProcess]:
    subprocess = client.spawn_child(
        args, process_name="pyspawner-test", sandbox_config=sandbox_config
    )
    try:
        yield subprocess
    finally:
        try:
            subprocess.stdout.read()
        except ValueError:
            pass  # stdout already closed
        try:
            subprocess.stderr.read()
        except ValueError:
            pass  # stderr already closed
        try:
            subprocess.kill()
        except ProcessLookupError:
            pass
        try:
            subprocess.wait(0)
        except ChildProcessError:
            pass


def _spawn_and_communicate(
    client: pyspawner.Client,
    indented_code: str,
    stdin: bytes = b"",
    chroot_dir: Optional[Path] = None,
    network_config: Optional[pyspawner.NetworkConfig] = None,
    skip_sandbox_except: FrozenSet[str] = frozenset(),
) -> Tuple[int, bytes, bytes]:
    """Spawn, execute `indented_code`, and return (exitcode, stdout, stderr).

    This will never error.
    """
    with _spawned_child_context(
        client,
        args=[indented_code],
        sandbox_config=pyspawner.SandboxConfig(
            chroot_dir=chroot_dir,
            network=network_config,
            skip_sandbox_except=skip_sandbox_except,
        ),
    ) as subprocess:
        subprocess.stdin.write(stdin)
        subprocess.stdin.close()
        stdout = subprocess.stdout.read()
        stderr = subprocess.stderr.read()
        subprocess.stdout.close()
        subprocess.stderr.close()
        _, status = subprocess.wait(0)
        if os.WIFSIGNALED(status):
            exitcode = -os.WTERMSIG(status)
        elif os.WIFEXITED(status):
            exitcode = os.WEXITSTATUS(status)
        else:
            raise OSError("Unexpected status: %d" % status)
        return exitcode, stdout, stderr


def _spawn_and_communicate_or_raise(
    client: pyspawner.Client,
    indented_code: str,
    chroot_dir: Optional[Path] = None,
    network_config: Optional[pyspawner.NetworkConfig] = None,
    skip_sandbox_except: FrozenSet[str] = frozenset(),
) -> None:
    """Like _spawn_and_communicate(), but raise if exit code is not 0."""
    exitcode, stdout, stderr = _spawn_and_communicate(
        client,
        indented_code,
        chroot_dir=chroot_dir,
        network_config=network_config,
        skip_sandbox_except=skip_sandbox_except,
    )
    self.assertEqual(exitcode, 0, "Exit code %d: %r" % (exitcode, stderr))
    self.assertEqual(stderr, b"", "Unexpected stderr: %r" % stderr)
    self.assertEqual(stdout, b"", "Unexpected stdout: %r" % stdout)


class PyspawnerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._client = pyspawner.Client(
            child_main="tests.test_client.child_main",
            environment={"LC_CTYPE": "C.UTF-8", "TEST_ENV": "yes"},
        )

    @classmethod
    def tearDownClass(cls):
        cls._client.close()
        del cls._client

    def setUp(self):
        super().setUp()
        self.chroot_dir = Path(tempfile.mkdtemp(prefix="pyspawner-test-chroot-"))
        self.chroot_dir.chmod(0o755)  # so subprocesses can read in their chroots

    def tearDown(self):
        shutil.rmtree(self.chroot_dir)
        super().tearDown()

    def test_stdout_stderr(self):
        exitcode, stdout, stderr = _spawn_and_communicate(
            self._client,
            r"""
            import os
            import sys
            print("stdout")
            print("stderr", file=sys.stderr)
            sys.__stdout__.write("__stdout__\n")
            sys.__stderr__.write("__stderr__\n")
            os.write(1, b"fd1\n")
            os.write(2, b"fd2\n")
            """,
        )
        self.assertEqual(exitcode, 0)
        self.assertEqual(stdout, b"stdout\n__stdout__\nfd1\n")
        self.assertEqual(stderr, b"stderr\n__stderr__\nfd2\n")

    def test_exception_goes_to_stderr(self):
        exitcode, stdout, stderr = _spawn_and_communicate(
            self._client, "import abaskjdgh"
        )
        self.assertEqual(exitcode, 1)
        self.assertEqual(stdout, b"")
        self.assertRegex(stderr, b"ModuleNotFoundError")

    def test_stdin(self):
        exitcode, stdout, stderr = _spawn_and_communicate(
            self._client,
            r"""
            import sys
            sys.stdout.write(sys.stdin.read())
            """,
            stdin=b"hello",
        )
        self.assertEqual(stderr, b"")
        self.assertEqual(stdout, b"hello")
        self.assertEqual(exitcode, 0)

    def test_SECURITY_use_environment(self):
        _spawn_and_communicate_or_raise(
            self._client,
            r"""
            import os
            env = dict(os.environ)
            assert env == {
                "LC_CTYPE": "C.UTF-8",
                "TEST_ENV": "yes",
            }, "Got wrong os.environ: %r" % env
            """,
        )

    def test_SECURITY_sock_and_any_other_fds_are_closed(self):
        # The user cannot access pipes or files outside its sandbox (aside from
        # stdout+stderr, which the parent process knows are untrusted).
        _spawn_and_communicate_or_raise(
            self._client,
            r"""
            import os
            for badfd in list(range(3, 20)):
                try:
                    os.write(badfd, b"x")
                    raise RuntimeError("fd %d is unexpectedly open" % badfd)
                except OSError as err:
                    assert err.args[0] == 9  # Bad file descriptor
            """,
        )

    def test_SECURITY_parent_ip_is_off_limits(self):
        # The module cannot access a service on its host
        hostname = socket.gethostname()
        host_ip = socket.gethostbyname(hostname)
        port = 19999  # arbitrary

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind((host_ip, port))
            s.listen(1)

            _spawn_and_communicate_or_raise(
                self._client,
                r"""
                import errno
                import socket
                try:
                    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                        s.connect((%r, %r))
                        assert False, "connect() should have failed"
                except OSError as err:
                    assert err.errno == errno.ECONNREFUSED
                """
                % (host_ip, port),
                network_config=pyspawner.NetworkConfig(),
            )

    def test_SECURITY_private_network_is_off_limits(self):
        # The module cannot access a service on the private network.
        # Try to connect to Postgres -- we know it's there.
        postgres_ip = "10.2.3.4"
        port = 5432

        _spawn_and_communicate_or_raise(
            self._client,
            r"""
            import errno
            import socket
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.connect((%r, %r))
                    assert False, "connect() should have failed"
            except OSError as err:
                assert err.errno == errno.ECONNREFUSED
            """
            % (postgres_ip, port),
            network_config=pyspawner.NetworkConfig(),
        )

    def test_SECURITY_network_none_means_no_networking(self):
        _spawn_and_communicate_or_raise(
            self._client,
            r"""
            import errno
            import socket
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                try:
                    s.connect(("1.1.1.1", 53))
                    assert False, "Connect should not work when network disabled"
                except OSError as err:
                    assert err.errno == errno.ENETUNREACH
            """,
            network_config=None,
        )

    # TODO enable external-network tests under some sort of tag.
    # (External-network tests can fail for reasons outside our control.)
    # In the meantime: if you're going to fiddle with iptables, remember to
    # uncomment these tests during development.
    # def test_network_external_dns(self):
    #     _spawn_and_communicate_or_raise(
    #         self._client,
    #         r"""
    #         import socket
    #         socket.gethostbyname("example.com")  # don't crash
    #         """,
    #         chroot_dir=READONLY_CHROOT.root,  # for /etc/resolv.conf et al
    #         network_config=pyspawner.NetworkConfig(),
    #     )
    #
    # def test_network_external_ip(self):
    #     _spawn_and_communicate_or_raise(
    #         self._client,
    #         r"""
    #         import socket
    #         with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
    #             s.settimeout(5)  # in case the test fails, fail fast
    #             s.connect(("1.1.1.1", 53))  # don't crash or timeout
    #         """,
    #         network_config=pyspawner.NetworkConfig(),
    #     )

    def test_SECURITY_no_capabilities(self):
        # Even if the user becomes root, the Linux "capabilities" system
        # restricts syscalls that might leak outside the container.
        _spawn_and_communicate_or_raise(
            self._client,
            r"""
            import ctypes
            import os
            libc = ctypes.CDLL("libc.so.6", use_errno=True)
            PR_CAP_AMBIENT = 47
            PR_CAP_AMBIENT_IS_SET = 1
            CAP_SYS_CHROOT = 18  # just one example
            EPERM = 1

            # Test a capability isn't set
            assert (
                libc.prctl(PR_CAP_AMBIENT, PR_CAP_AMBIENT_IS_SET, CAP_SYS_CHROOT, 0, 0)
            ) == 0
            # Test we can't actually *use* a capability -- chroot, for example

            try:
                os.chroot("/")  # raise on error
                assert False, "chroot worked after dropping capabilities?"
            except PermissionError:
                pass
            """,
            skip_sandbox_except=frozenset(["drop_capabilities"]),
        )

    def test_SECURITY_prevent_writing_uid_map(self):
        _spawn_and_communicate_or_raise(
            self._client,
            r"""
            from pathlib import Path

            def assert_write_fails(path: str, text: str):
                try:
                    Path(path).write_text(text)
                except PermissionError:
                    pass
                else:
                    assert False, "Write to %s should have failed" % path

            assert_write_fails("/proc/self/uid_map", "0 0 65536")
            assert_write_fails("/proc/self/setgroups", "allow")
            assert_write_fails("/proc/self/gid_map", "0 0 65536")
            """,
            # There's no way to disable this security feature. But for testing
            # we must _disable_ setuid and chroot; so write a dummy
            # skip_sandbox_except to accomplish that.
            skip_sandbox_except=frozenset(["skip_all_optional_sandboxing"]),
        )

    def test_SECURITY_chroot_has_no_proc_dir(self):
        _spawn_and_communicate_or_raise(
            self._client,
            r"""
            import os

            assert not os.path.exists("/proc"), "/proc should not be accessible"
            assert not os.path.exists("/sys"), "/sys should not be accessible"
            """,
            chroot_dir=self.chroot_dir,
            skip_sandbox_except=frozenset(["none"]),
        )

    def test_SECURITY_chroot_ensures_cwd_is_under_root(self):
        _spawn_and_communicate_or_raise(
            self._client,
            r"""
            import os

            assert os.getcwd() == "/"
            """,
            chroot_dir=self.chroot_dir,
            skip_sandbox_except=frozenset(["none"]),
        )

    def test_SECURITY_can_exec_binaries_in_chroot(self):
        arch = platform.machine()
        shutil.copy2("tests/hello-world." + arch, self.chroot_dir / "hello-world")

        _spawn_and_communicate_or_raise(
            self._client,
            r"""
            import subprocess
            result = subprocess.run(["/hello-world"], capture_output=True)
            assert result.stderr == b"", "program errored %r" % result.stderr
            assert result.stdout == b"Hello, world!\n", "program output %r" % result.stdout
            assert result.returncode == 0, "program exited with status code %d" % result.returncode
            """,
            chroot_dir=self.chroot_dir,
            skip_sandbox_except=frozenset(["none"]),
        )

    def test_SECURITY_setuid(self):
        # The user is cannot setuid(0) because UID 1000 has no capabilities
        #
        # This tests setuid and drop_capabilities sandbox features. See also
        # test_SECURITY_seccomp(), which overrides this one. (On production,
        # seccomp will kill -31 the process; the kernel will never get a chance
        # to set EPERM.)
        _spawn_and_communicate_or_raise(
            self._client,
            r"""
            import os
            assert os.getuid() == 1000
            assert os.getgid() == 1000
            # Assert the script can't setuid() to anything else. In other
            # words: test we really used setresuid(), not setuid() -- because
            # setuid() lets you un-setuid() later.
            try:
                os.setuid(0); assert False, "gah, how did we setuid to 0?"
            except PermissionError:
                pass  # good
            """,
            chroot_dir=self.chroot_dir,
            skip_sandbox_except=frozenset(["setuid", "drop_capabilities"]),
        )

    def test_SECURITY_seccomp(self):
        # The user cannot call forbidden syscalls.
        #
        # We test setuid() because it's an obvious one. See also
        # test_SECURITY_setuid(), which tests that seccomp is not the only
        # thing protecting us from setuid.
        exitcode, stdout, stderr = _spawn_and_communicate(
            self._client,
            r"""
            import os
            os.setuid(2)
            """,
            chroot_dir=self.chroot_dir,
            skip_sandbox_except=frozenset(["seccomp", "no_new_privs"]),
        )
        self.assertEqual(exitcode, -31)

    def test_SECURITY_no_new_privs(self):
        # The user cannot use a setuid program to become root
        assert os.getuid() == 0  # so our test suite can actually chmod
        # Build the tempfile in the root filesystem, where there's no
        # "nosetuid" mount option
        with tempfile.NamedTemporaryFile(
            prefix="print-id", suffix=".bin", dir="/"
        ) as prog:
            prog.close()
            # We can't test with a _script_: we need to test with a _binary_.
            # (Scripts invoke the interpreter, which is not setuid.)
            #
            # The "id" binary is perfect: it prints all three uids and gids if
            # they differ from one another.
            shutil.copy("/usr/bin/id", prog.name)
            os.chown(prog.name, 0, 0)  # make doubly sure root owns it
            os.chmod(prog.name, 0o755 | stat.S_ISUID | stat.S_ISGID)
            exitcode, stdout, stderr = _spawn_and_communicate(
                self._client,
                r"""
                import os
                os.execv(%r, [%r])
                """
                % (prog.name, prog.name),
                # XXX SECURITY [2019-10-11] This test should fail if we comment
                # out "no_new_privs". Why doesn't it? (It looks like there's
                # some other security layer we don't know of....)
                skip_sandbox_except=frozenset(["setuid", "no_new_privs"]),
            )
            if stderr:
                assert False, stderr
            self.assertEqual(exitcode, 0)
            self.assertEqual(stdout, b"uid=1000 gid=1000 groups=1000\n")


class ClientTest(unittest.TestCase):
    def test_executable(self):
        with pyspawner.Client(
            child_main="tests.test_client.child_main",
            environment={"LC_CTYPE": "C.UTF-8"},
            executable="/usr/bin/python3.8",
        ) as client:
            _spawn_and_communicate_or_raise(
                self._client,
                r"""
                import sys
                assert sys.executable == "/usr/bin/python3.8"
                """,
            )
