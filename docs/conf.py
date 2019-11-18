import os
import sys

sys.path.insert(0, os.path.abspath(".."))

import pyspawner


project = "pyspawner"
copyright = "2019, Adam Hooper"
author = "Adam Hooper"
version = pyspawner.__version__.rsplit(".", 1)[0]
release = pyspawner.__version__
templates_path = ["_templates"]
source_suffix = ".rst"
extensions = ["sphinx.ext.autodoc", "sphinx_autodoc_typehints"]
master_doc = "index"
pygments_style = "sphinx"
html_static_path = ["_static"]
