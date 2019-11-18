docker build . && docker run --rm --security-opt seccomp=docker/pyspawner-seccomp-profile.json --cap-add NET_ADMIN $(docker build . -q)
