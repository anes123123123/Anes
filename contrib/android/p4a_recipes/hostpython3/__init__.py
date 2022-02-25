import os

from pythonforandroid.recipes.hostpython3 import HostPython3Recipe
from pythonforandroid.util import load_source

util = load_source('util', os.path.join(os.path.dirname(os.path.dirname(__file__)), 'util.py'))


assert HostPython3Recipe.depends == []
assert HostPython3Recipe.python_depends == []


class HostPython3RecipePinned(util.InheritedRecipeMixin, HostPython3Recipe):
    version = "3.8.12"
    sha512sum = "f7227c9d82c37a3f21d4e4ec352b75424d8103b5144e7accec13dca626c79268db76143782629131525a07bb026630e55fccd4381bd78990b3561cc565681190"


recipe = HostPython3RecipePinned()
