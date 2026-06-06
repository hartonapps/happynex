import subprocess
import sys
import os


def add_to_requirements(package_name: str):
    """Add a package to requirements.txt if not already present."""
    req_file = os.path.join(os.path.dirname(__file__), 'requirements.txt')
    if not os.path.exists(req_file):
        with open(req_file, 'w', encoding='utf-8') as f:
            f.write('')

    with open(req_file, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # check if package is already in requirements
    if package_name.lower() in content.lower():
        return False  # already present
    
    # append package to requirements.txt
    with open(req_file, 'a', encoding='utf-8') as f:
        f.write(f'\n{package_name}')
    return True  # added


def install_package(package_name: str):
    """Install a Python package using pip."""
    try:
        subprocess.check_call([sys.executable, '-m', 'pip', 'install', package_name])
        return True
    except subprocess.CalledProcessError:
        return False


def ensure_plugin_deps(plugin_module):
    """Check if plugin has __requires__ and install missing deps."""
    requires = getattr(plugin_module, '__requires__', None)
    if not requires:
        return []  # no deps needed
    
    if isinstance(requires, str):
        requires = [requires]
    
    installed = []
    for pkg in requires:
        # try to install and add to requirements
        if install_package(pkg):
            if add_to_requirements(pkg):
                installed.append(pkg)
    
    return installed
