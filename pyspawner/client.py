import os
import socket
import subprocess
import sys
import threading
from dataclasses import dataclass
from typing import Any, BinaryIO, Dict, List, Optional, Tuple

from . import protocol


@dataclass(frozen=True)
class ChildProcess:
    """
    A handle for the parent to interact with a spawned child process.

    This is akin to a subprocess.Popen object ... but with fewer features.
    (Rationale: subprocess.Popen has too many features.)
    """

    pid: int
    """
    Child process ID as seen from the parent.

    (The child process will see its own ID as ``1``.)
    """

    stdin: BinaryIO  # auto-closes in __del__
    """
    Writable pipe, readable in the child as ``sys.stdin``.
    """

    stdout: BinaryIO  # auto-closes in __del__
    """
    Readable pipe, written in the child as ``sys.stdout``.
    """

    stderr: BinaryIO  # auto-closes in __del__
    """
    Readable pipe, written in the child as ``sys.stderr``.
    """

    def kill(self) -> None:
        """
        Terminate the child process with ``SIGKILL``.
        """
        return os.kill(self.pid, 9)

    def wait(self, options: int) -> Tuple[int, int]:
        """
        Wait for the child process to complete.

        You *must* call this for every child process. Otherwise, children will
        become zombie processes when they terminate, consuming system
        resources.
        """
        return os.waitpid(self.pid, options)


def _encode_module_name_list(l: List[str]) -> str:
    for s in l:
        if '"' in s or "," in s or " " in s:
            raise ValueError("Module name %r is illegal" % s)
    return ",".join(l)


class Client:
    """
    Launch Python quickly, sharing most memory pages.

    The problem this solves: we want to spin up many children quickly; but as
    soon as a child starts running we can't trust it. Starting Python with lots
    of imports like Pyarrow+Pandas can take ~2s and cost ~100MB RAM.

    The solution: a mini-server process, the "pyspawner", preloads Python
    modules. Then we clone() each time we need a subprocess. (clone() is
    near-instantaneous.) Beware: since clone() copies all memory, the
    "pyspawner" shouldn't load anything sensitive before clone(). (No Django:
    it reads secrets!)

    This is similar to Python's multiprocessing.forkserver, except...:

    * Children are not managed. It's up to the caller to kill and wait for the
      process. Children are direct children of the _caller_, not of the
      pyspawner. (We use CLONE_PARENT.)
    * asyncio-safe: we don't listen for SIGCHLD, because asyncio's
      subprocess-management routines override the signal handler.
    * Thread-safe: multiple threads may spawn multiple children, and they may
      all run concurrently (unless child code writes files or uses networking).
    * No `multiprocessing.context`. This Client is the context.
    * No `Connection` (or other high-level constructs).
    * The caller interacts with the pyspawner process via _unnamed_ AF_UNIX
      socket, rather than a named socket. (`multiprocessing` writes a pipe
      to /tmp.) No messing with hmac. Instead, we mess with locks. ("Aren't
      locks worse?" -- [2019-09-30, adamhooper] probably not, because clone()
      is fast; and multiprocessing and asyncio have a race in Python 3.7.4 that
      causes forkserver children to exit with status code 255, so their
      named-pipe+hmac approach does not inspire confidence.)

    :param child_main: The full name (including module name) of the function
                       each child should run. (Must be importable.)
    :param environment: Environment variables for child processes. (Must all
                        be str.)
    :param preload_imports: List of module names pyspawner should import at
                            startup. These modules (plus pyspawner's internal
                            imports) will be preloaded in all child processes.
    :param executable: Python executable to invoke. (Default: current-process
                       executable).
    """

    def __init__(
        self,
        *,
        child_main: str,
        environment: Dict[str, str] = {},
        preload_imports: List[str] = [],
        executable: str = sys.executable,
    ):
        # We rely on Python's os.fork() internals to close FDs and run a child
        # process.
        self._socket, child_socket = socket.socketpair(socket.AF_UNIX)
        self._process = subprocess.Popen(
            [
                executable,
                "-u",  # PYTHONUNBUFFERED: parents read children's data sooner
                "-c",
                'import pyspawner.main; pyspawner.main.pyspawner_main("%s", "%s", %d)'
                % (
                    child_main,
                    _encode_module_name_list(preload_imports),
                    child_socket.fileno(),
                ),
            ],
            # SECURITY: children inherit these values
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=sys.stdout.fileno(),
            stderr=sys.stderr.fileno(),
            close_fds=True,
            pass_fds=[child_socket.fileno()],
        )
        child_socket.close()
        self._lock = threading.Lock()
        self._closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def spawn_child(
        self,
        args: List[Any] = [],
        *,
        process_name: Optional[str] = None,
        sandbox_config: protocol.SandboxConfig,
    ) -> ChildProcess:
        """
        Make our server spawn a process, and return it.

        :param args: List of arguments to pass to the child-process function.
                     (Must be picklable.)
        :param process_name: Process name to display for the child process in
                             ``ps`` and other sysadmin tools. (Useful for
                             debugging.)
        :param sandbox_config: Sandbox settings.
        :type sandbox_config: pyspawner.SandboxConfig
        :raises OSError: if the clone() system call fails.
        :raises pyroute2.NetlinkError: if network configuration fails.
        :rtype: pyspawner.ChildProcess
        """
        message = protocol.SpawnChild(
            process_name=process_name, args=args, sandbox_config=sandbox_config
        )
        with self._lock:
            message.send_on_socket(self._socket)
            response = protocol.SpawnedChild.recv_on_socket(self._socket)
        return ChildProcess(
            pid=response.pid,
            stdin=os.fdopen(response.stdin_fd, mode="wb"),
            stdout=os.fdopen(response.stdout_fd, mode="rb"),
            stderr=os.fdopen(response.stderr_fd, mode="rb"),
        )

    def close(self) -> None:
        """
        Kill the pyspawner.

        Spawned child processes continue to run: they are entirely disconnected
        from their pyspawner.
        """
        with self._lock:
            if not self._closed:
                self._socket.close()  # inspire self._process to exit of its own accord
                self._process.wait()
                self._closed = True
