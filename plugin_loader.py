import ast
import importlib
import importlib.util
import pkgutil
import os
from plugin_manager import ensure_plugin_deps, install_package

PLUGINS_PACKAGE = 'plugins'


def parse_plugin_requires(plugin_path: str):
    try:
        with open(plugin_path, 'r', encoding='utf-8') as f:
            tree = ast.parse(f.read(), plugin_path)
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if getattr(target, 'id', None) == '__requires__':
                        return ast.literal_eval(node.value)
    except Exception:
        return None
    return None


def load_plugins():
    plugins = {}
    new_deps = []
    package_dir = os.path.join(os.path.dirname(__file__), PLUGINS_PACKAGE)
    if not os.path.isdir(package_dir):
        return plugins, new_deps

    for finder, name, ispkg in pkgutil.iter_modules([package_dir]):
        mod_name = f"{PLUGINS_PACKAGE}.{name}"
        plugin_file = os.path.join(package_dir, f"{name}.py")
        mod = None
        try:
            try:
                mod = importlib.import_module(mod_name)
            except (ModuleNotFoundError, ImportError):
                requires = parse_plugin_requires(plugin_file)
                if requires:
                    if isinstance(requires, str):
                        requires = [requires]
                    for pkg in requires:
                        install_package(pkg)
                mod = importlib.import_module(mod_name)

            deps_installed = ensure_plugin_deps(mod)
            if deps_installed:
                new_deps.extend(deps_installed)
                mod = importlib.reload(mod)

            help_meta = getattr(mod, '__help__', None)
            run_fn = getattr(mod, 'run', None)
            if help_meta and run_fn:
                for cmd in help_meta.get('commands', []):
                    plugins[cmd] = {'module': mod, 'help': help_meta, 'run': run_fn}
        except Exception:
            continue

    return plugins, new_deps
