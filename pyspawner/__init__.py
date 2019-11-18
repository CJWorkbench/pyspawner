"""
Subprocess that spawns children quickly, using clone().

How to use
~~~~~~~~~~

Create a :class:`pyspawner.Client` that imports the "common" Python imports
your sandboxed code will run. (These ``import`` statements aren't sandboxed,
so be sure you trust the Python modules.)

Then call :meth:`pyspawner.Client.spawn_child()` each time you want to create
a new child. It will invoke the pyspawner's ``child_main`` function with the
given arguments.

Here's pseudo-code for invoking the pyspawner part::

    import pyspawner

    # pyspawner.Client() is slow; ideally, you'll just call it during startup.
    with pyspawner.Client(
        child_main="mymodule.main",
        environment={"LC_ALL": "C.UTF-8"},
        preload_imports=["pandas"],  # put all your slow imports here
    ) as cloner:
        # cloner.spawn_child() is fast; call it as many times as you like.
        child_process: pyspawner.ChildProcess = cloner.spawn_child(
            args=["arg1", "arg2"],  # List of picklable Python objects
            process_name="child-1",
            sandbox_config=pyspawner.SandboxConfig(
                chroot_dir=Path("/path/to/chroot/dir"),
                network=pyspawner.NetworkConfig()
            )
        )

        # child_process has .pid, .stdin, .stdout, .stderr.
        # Read from its stdout and stderr, and then wait for it.

For each child, read from stdout and stderr until end-of-file; then wait() for
the process to exit. Reading from two pipes at once is a standard exercise in
UNIX, so the minutae are left as an exercise. A safe approach:

1. Register both stdout and stderr in a :class:`selectors.DefaultSelector`
2. loop, calling :meth:`selectors.BaseSelector.select()` and reading from
   whichever file descriptors have data. Unregister whichever file descriptors
   reach EOF; and read but _ignore_ data past a predetermined buffer size. Kill
   the child process if this is taking too long. (Keep reading after killing
   the child to avoid deadlock.)
3. Wait for the child process (using :func:`os.waitpid()`) to clean up its
   system resources.
"""
from .client import ChildProcess, Client
from .protocol import NetworkConfig, SandboxConfig

__all__ = ["Client", "ChildProcess", "NetworkConfig", "SandboxConfig"]

__version__ = "0.9.0"
