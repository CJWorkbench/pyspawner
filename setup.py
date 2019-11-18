#!/usr/bin/env python

import os.path
from setuptools import setup

from pyspawner import __version__

# We use the README as the long_description
readme = open(os.path.join(os.path.dirname(__file__), "README.rst")).read()

setup(
    name="pyspawner",
    version="0.9.0",
    author="Adam Hooper",
    author_email="adam@adamhooper.com",
    url="https://github.com/CJWorkbench/pyspawner",
    description="Launch Python environments quickly, using Linux's clone() syscall.",
    long_description=readme,
    long_description_content_type="text/x-rst",
    license="BSD",
    zip_safe=False,
    packages=["pyspawner"],
    package_data={"pyspawner": ["sandbox-seccomp.bpf"]},
    install_requires=["pyroute2~=0.5.7"],
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "Topic :: Software Development :: Libraries :: Python Modules",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.7",
    ],
)
