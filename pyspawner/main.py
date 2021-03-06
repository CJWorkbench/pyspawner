import importlib
import os
import socket
import sys
import textwrap
import traceback
from pathlib import Path
from typing import Callable, Optional

from . import c, clonefds, protocol
from .sandbox import sandbox_child_from_pyspawner, sandbox_child_self

# GLOBAL VARIABLES
#
# SECURITY: _any_ variable in "pyspawner" is accessible to a child it spawns.
# `del` will not delete the data.
#
# Our calling convention is: "pyspawner uses global variables; child can see
# them." Rationale: to a malicious child, all variables are global anyway.
# "pyspawner" should use very few variables, and they are all listed here.
#
# This data must all be harmless when in the hands of a malicious user. (For
# instance: a closed file descriptor is harmless.)
child_main: Optional[Callable[..., None]] = None
"""Function to call after sandboxing, with *message.args."""
sock: Optional[socket.socket] = None
"""Socket "pyspawner" uses to communicate with its parent."""
message: Optional[protocol.SpawnChild] = None
"""Arguments passed to the spawned child."""
clone_fds: Optional[clonefds.CloneFds] = None
"""File descriptors created and copied for each clone."""


def run_child() -> None:
    global sock, clone_fds, message

    # Read and clear `message`
    process_name = message.process_name
    sandbox_config = message.sandbox_config
    args = message.args
    message = None

    # Close `sock`.
    # SECURITY: if we forget this, the child could read all the parent's
    # messages! It's super-important.
    os.close(sock.fileno())
    sock = None

    # Set process name seen in "ps". Helps find PID when debugging.
    # (Do this before sandboxing to help when debugging our sandboxing code.)
    if process_name:
        c.libc_prctl_pr_set_name(process_name)

    # Wait before sandboxing; and replace all our file descriptors.
    #
    # Before this, messages to stdout/stderr go wherever pyspawner's
    # stdout/stderr go.
    #
    # After this, messages to stdout/stderr must be read by the parent process.
    child_fds = clone_fds.become_child()
    std_fds = child_fds.wait_for_namespace_ready()
    std_fds.replace_this_process_standard_fds()
    clone_fds = None

    # Sandbox ourselves.
    sandbox_child_self(sandbox_config)

    # Run the child code. This is what it's all about!
    #
    # It's normal for child code to raise an exception. That's probably a
    # developer error, and it's best to show the developer the problem --
    # exactly what `stderr` is for. So we log exceptions to stderr. (This is
    # the stderr that the parent process must read, remember. We closed
    # pyspawner's stderr.)
    #
    # SECURITY: it's possible for a child to try and fiddle with the stack or
    # heap to execute anything in memory. (Think "goto"). child_main() might
    # never return. That's okay -- we're sandboxed, so the only harm is a waste
    # of CPU cycles. The parent should kill us after a timeout.)
    try:
        child_main(*args)
        os._exit(0)
    except:
        traceback.print_exc()
        os._exit(1)


def spawn_child(sock: socket.socket, message: protocol.SpawnChild) -> None:
    """
    Fork a child process; send its handle over `sock`; return.

    This closes all open file descriptors in the child: stdin, stdout, stderr,
    and `sock.fileno()`. The reason is SECURITY: the child will invoke
    user-provided code, so we bar everything it doesn't need. (Heck, it doesn't
    even get stdin+stdout+stderr!)

    There are three processes running concurrently here:

    * "parent": the Python process that holds a Pyspawner handle. It sent
                `SpawnChild` on `sock` and expects a response of `SpawnedChild`
                (with "child_pid").
    * "pyspawner": the pyspawner_main() process. It called this function. It
                    has few file handles open -- by design. It spawns "child",
                    and sends "parent" the "child_pid" over `sock`.
    * "child": invokes `child_main()`.
    """
    global clone_fds
    assert clone_fds is None  # previous spawn_child() cleaned up after itself

    clone_fds = clonefds.CloneFds.create()

    try:
        child_pid = c.libc_clone(run_child)
    except PermissionError:
        seccomp_profile_path = (
            "/path/to/pyspawner/docker/pyspawner-seccomp-profile.json"
        )

        sys.stderr.write(
            textwrap.dedent(
                r"""
                *** pyspawner failed to use the clone() system call. ***

                Are you using pyspawner in Docker? Docker's default seccomp
                profile disallows using clone() to create Linux containers. To
                use pyspawner you'll need to relax this restriction:

                    docker run \
                        --security-opt seccomp="%s" \
                        ...
                """
                % seccomp_profile_path
            )
        )
        raise

    # Sandbox the child from the pyspawner side of things. (To avoid races,
    # the child waits for us to close is_namespace_ready_write_fd before
    # continuing with its own sandboxing.)
    #
    # Do this sandboxing before returning the PID to the parent process.
    # Otherwise, the parent could kill the process before we're done sandboxing
    # it (and we'd need to recover from that race).
    pyspawner_fds = clone_fds.become_pyspawner()
    sandbox_child_from_pyspawner(child_pid, message.sandbox_config)
    parent_fds = pyspawner_fds.signal_namespace_is_ready()

    # Send our lovely new process to the caller (parent process)
    spawned_child = protocol.SpawnedChild(
        child_pid, parent_fds.stdin_w, parent_fds.stdout_r, parent_fds.stderr_r
    )
    spawned_child.send_on_socket(sock)

    parent_fds.close()
    clone_fds = None


def pyspawner_main(_child_main: str, preload_imports_str: str, socket_fd: int) -> None:
    """
    Start the pyspawner.

    The init protocol ("a" means "parent" [class Pyspawner], "b" means,
    "pyspawner" [pyspawner_main()]; "c" means, "child" [run_child()]):

    1a. Parent invokes pyspawner_main(), passing imports and AF_UNIX fd as
        arguments.
    2b. Pyspawner imports modules in its main (and only) thread.
    3b. Pyspawner calls socket.fromfd(), establishing a socket connection. It
        waits for messages from parent.
    4a. Parent sends a message with spawn parameters.
    4b. Pyspawner opens pipes for file descriptors, calls clone(), and sends
        a response to Parent with pid and (stdin, stdout, stderr) descriptors.
    4c. Child waits for pyspawner to close its file descriptors.
    5a. Parent receives PID and descriptors from Pyspawner.
    5b. Pyspawner closes its file descriptors and waits for parent again.
    5c. Child sandboxes itself and calls child code with stdin/stdout/stderr.
    6a. Parent writes to child's stdin, reads from its stdout, and waits for
        its PID.
    6c. Child reads from stdin, writes to stdout, and exits.

    For shutdown, the client simply closes its connection.

    The inevitable race: if "parent" doesn't read "child_pid" from the other
    end of "sock" and wait() for it, then nothing will wait() for the child
    process after it dies and it will become a zombie child of "parent".
    """
    # Load the function we'll call in clone() children
    global child_main
    child_main_module_name, child_main_name = _child_main.rsplit(".", 1)
    child_main_module = importlib.import_module(child_main_module_name)
    child_main = child_main_module.__dict__[child_main_name]

    # 2b. Pyspawner imports modules in its main (and only) thread
    for im in preload_imports_str.split(","):
        if im:
            __import__(im)

    # 3b. Pyspawner establishes socket connection
    #
    # Note: we don't put this in a `with` block, because that would add a
    # finalizer. Finalizers would run in the "child" process; but our child
    # closes the socket as a security precaution, so the finalizer would
    # crash.
    #
    # (As a rule, pyspawner shouldn't use try/finally or context managers.)
    global sock  # see GLOBAL VARIABLES comment
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM, fileno=socket_fd)

    while True:
        # 4a. Parent sends a message with spawn parameters.
        global message  # see GLOBAL VARIABLES comment
        try:
            # raise EOFError, RuntimeError
            message = protocol.SpawnChild.recv_on_socket(sock)
        except EOFError:
            # shutdown: client closed its connection
            return

        # 4b. Pyspawner calls clone() and sends a response to Parent.
        spawn_child(sock, message)
