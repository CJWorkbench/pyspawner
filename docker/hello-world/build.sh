#!/bin/bash

set -ex
docker build .
image=$(docker build -q .)

arch=$(uname -m)
path="$(realpath ../../tests/hello-world.$arch)"
touch "$path"

docker run -v "$path:/out" $image cp /hello-world /out
chmod +x "$path"
