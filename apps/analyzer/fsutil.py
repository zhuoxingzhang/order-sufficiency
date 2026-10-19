"""File helpers: long Windows paths and source walking."""
import os

SKIP_DIRS = {'spec', 'test', 'tests', 'node_modules', 'vendor', 'fixtures', 'locales', '.git', 'tmp',
             'public', 'frontend', 'javascript', 'assets', 'stories'}


def lp(path):
    """Path usable beyond MAX_PATH on Windows."""
    if os.name != 'nt':
        return path
    p = os.path.abspath(path)
    return p if p.startswith('\\\\?\\') else '\\\\?\\' + p


def rel(path, root):
    return os.path.relpath(path.replace('\\\\?\\', ''), root.replace('\\\\?\\', '')).replace('\\', '/')


def walk(root, exts, skip=SKIP_DIRS):
    """Yield source files under root with the given extensions, skipping test and asset trees."""
    for d, dirs, fs in os.walk(lp(root)):
        dirs[:] = [x for x in dirs if x not in skip]
        for f in fs:
            if f.endswith(exts):
                yield os.path.join(d, f)


def read(path):
    with open(lp(path), encoding='utf-8', errors='replace') as f:
        return f.read()
