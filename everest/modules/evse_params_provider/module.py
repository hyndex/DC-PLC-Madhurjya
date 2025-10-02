import os
import sys

# Ensure the local 'python' package is importable
_here = os.path.dirname(__file__)
_py_dir = os.path.join(_here, 'python')
if _py_dir not in sys.path:
    sys.path.insert(0, _py_dir)

# Expose the module class under the expected name
from module import EvseParamsProvider as Module  # type: ignore

