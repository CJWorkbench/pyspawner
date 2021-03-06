Clone sandboxed Python processes quickly and securely.

Documentation
=============

Documentation is available at `https://pyspawner.readthedocs.io
<https://pyspawner.readthedocs.io/>`_.

Usage
=====

Create a ``pyspawner.Client`` that imports the "common" Python imports
your sandboxed code will run. (These ``import`` statements aren't sandboxed,
so be sure you trust the Python modules.)

Then call ``pyspawner.Client.spawn_child()`` each time you want to create
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

1. Register both stdout and stderr in a ``selectors.DefaultSelector``
2. loop, calling ``selectors.BaseSelector.select()`` and reading from
   whichever file descriptors have data. Unregister whichever file descriptors
   reach EOF; and read but _ignore_ data past a predetermined buffer size. Kill
   the child process if this is taking too long. (Keep reading after killing
   the child to avoid deadlock.)
3. Wait for the child process (using ``os.waitpid()``) to clean up its
   system resources.

Setting up your environment
===========================

Your system must have ``libcap.so.2`` installed.

Pyspawner relies on Linux's ``clone()`` system call to create child-process
containers. If you're using pyspawner from a Docker container, subcontainer
are disabled by default. Run Docker with
``--seccomp-opt=/path/to/pyspawner/docker/pyspawner-seccomp-profile.json`` to
allow creating subcontainers.

By default, sandboxed children cannot access the Internet. If you want to
enable networking for child processes, ensure your process has the
``CAP_NET_ADMIN`` capability. (``docker run --cap-add NET_ADMIN ...``).
Also, you'll need to configure NAT in the parent-process environment ...
which is beyond the scope of this README. Finally, you may want to supply a
``chroot_dir`` to give child processes a custom ``/etc/resolv.conf``.

Ideally, sandboxed children would not be able to write anywhere on the main
filesystem. Unfortunately, the ``umount()`` and ``pivot_root()`` system calls
are restricted in many environments. As a placeholder, you're encouraged to
supply a ``chroot_dir`` to provide an environment for your sandboxed child
code. ``chroot_dir`` must be in a separate filesystem from the root filesystem.
(In the future, when the Linux container ecosystem evolves enough,
``chroot_dir`` will make children unmount the root filesystem.) Again, chroot
is beyond the scope of this README.


Developing
==========

The test suite depends on Docker. (Security tests involve temporary files
outside of temporary directories, iptables rules and setuid-0 files.)

Run ``./test.sh`` to test.

To add or fix features:

1. Write a test in ``tests/`` that breaks.
2. Write code in ``pyspawner/`` that makes the test pass.
3. Submit a pull request.


Releasing
=========

1. Run ``./test.sh`` and ``sphinx-build docs docs/build`` to check for errors.
2. Write a new version in ``pyspawner/__init__.py``. Use semver -- e.g., ``1.2.3``.
3. Write a ``CHANGELOG.rst`` entry.
4. ``git commit``
5. ``git tag VERSION`` (use semver with a ``v`` -- e.g., ``v1.2.3``)
6. ``git push --tags && git push``
7. ``python3 ./setup.py sdist``
8. ``twine upload dist/*``


License
=======

MIT. See ``LICENSE.txt``.
