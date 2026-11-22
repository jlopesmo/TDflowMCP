"""Embedded-module loader for TDflowMCP.tox (TD adapter; runs on TouchDesigner's main thread).

The component's Python core lives in Text DATs (one per module, under ./src). ``install_from_comp``
snapshots those texts on the main thread, verifies each against the SHA-256 recorded at build time
in ./manifest, and installs a ``sys.meta_path`` finder that serves exactly the manifest's
``tdflowmcp.*`` modules from that snapshot. Every module is imported eagerly here, so handler threads
never trigger an import and never read a DAT.

This file has no top-level ``td`` import, so it can be unit-tested with a fake component.
"""
import hashlib
import importlib.abc
import importlib.util
import json
import sys

FINDER_ID = "tdflowmcp-embedded"


def norm(text: str) -> str:
    return text.replace("\r\n", "\n")


def digest(text: str) -> str:
    return hashlib.sha256(norm(text).encode("utf-8")).hexdigest()


class EmbeddedFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    finder_id = FINDER_ID

    def __init__(self, sources, packages, build_id):
        self.sources = dict(sources)
        self.packages = set(packages)
        self.build_id = build_id

    def find_spec(self, fullname, path=None, target=None):
        if fullname not in self.sources:
            return None
        return importlib.util.spec_from_loader(fullname, self, is_package=fullname in self.packages)

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        name = module.__name__
        module.__file__ = f"<tdflowmcp-embedded:{name}>"
        module.__tdflow_build__ = self.build_id
        if name in self.packages:
            module.__path__ = []
        exec(compile(self.sources[name], module.__file__, "exec"), module.__dict__)


class LoaderError(Exception):
    pass


def snapshot(manifest, read_text):
    """Return (sources, packages) from the manifest, verifying every module's SHA-256."""
    sources, packages = {}, set()
    for name, info in manifest["modules"].items():
        text = norm(read_text(info["dat"]))
        if digest(text) != info["sha256"]:
            raise LoaderError(f"embedded module {name} does not match its build hash")
        sources[name] = text
        if info.get("package"):
            packages.add(name)
    return sources, packages


def install(sources, packages, build_id):
    """Install the finder at the front of sys.meta_path. Modules from another build or from disk
    with the same names are evicted first, so this component always runs its own embedded code."""
    sys.meta_path[:] = [f for f in sys.meta_path if getattr(f, "finder_id", None) != FINDER_ID]
    for name in list(sys.modules):
        if name in sources and getattr(sys.modules[name], "__tdflow_build__", None) != build_id:
            del sys.modules[name]
    finder = EmbeddedFinder(sources, packages, build_id)
    sys.meta_path.insert(0, finder)
    for name in sorted(sources):            # parents first (sorted puts 'a' before 'a.b')
        __import__(name)
    return finder


def install_from_comp(comp):
    manifest = json.loads(comp.op("manifest").text)
    src = comp.op("src")

    def read_text(dat_name):
        d = src.op(dat_name)
        if d is None:
            raise LoaderError(f"missing embedded module DAT {dat_name}")
        return d.text
    sources, packages = snapshot(manifest, read_text)
    # Build id recomputed from the verified module hashes (review 5): two components share loaded
    # modules only if their embedded code is identical.
    build_id = hashlib.sha256(json.dumps(sorted((n, m["sha256"]) for n, m in manifest["modules"].items()))
                              .encode("utf-8")).hexdigest()
    install(sources, packages, build_id)
    return manifest
