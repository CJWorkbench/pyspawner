FROM python:3.7.5-slim-buster

# libcap2: used by pyspawner (via ctypes) to drop capabilities
# iproute2: used by setup-sandbox.sh (for testing)
# iptables2: used by setup-sandbox.sh (for testing)
RUN mkdir -p /usr/share/man/man1 /usr/share/man/man7 \
    && apt-get update \
    && apt-get install --no-install-recommends -y \
        iproute2 \
        iptables \
        libcap2 \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir /app
WORKDIR /app

COPY ./setup.py README.rst /app/
RUN true \
      && mkdir /app/pyspawner \
      && echo '__version__ = "unused"' > /app/pyspawner/__init__.py \
      && python3 ./setup.py develop \
      && rm -rf /app/pyspawner

COPY pyspawner/ /app/pyspawner/
COPY tests/ /app/tests/

RUN python3 ./setup.py install

CMD [ "bash", "-xc", "tests/setup-sandbox.sh && python3 ./setup.py test" ]
