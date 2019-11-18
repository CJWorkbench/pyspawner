FROM debian:buster

RUN true \
      && apt-get update \
      && apt-get install -y \
          build-essential \
      && true

COPY main.c /src/main.c
RUN gcc -static -Wall -o /hello-world /src/main.c
