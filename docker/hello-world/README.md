A statically-linked program that prings "Hello, world!\n"

We use this to test forking programs from a chroot. It's statically linked so
we don't need extra files in the chroot.

To rebuild chdir here and `./build.sh`. The result will be written to a
platform-specific binary in `tests/` -- e.g., `tests/hello-world.x86_64`.
