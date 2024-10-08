from glob import glob
from setuptools import setup
from owrx.version import looseversion

try:
    from setuptools import find_namespace_packages
except ImportError:
    from setuptools import PEP420PackageFinder

    find_namespace_packages = PEP420PackageFinder.find

setup(
    name="OpenWebRX",
    version=str(looseversion),
    packages=find_namespace_packages(
        include=[
            "owrx*",
            "csdr*",
            "htdocs",
        ]
    ),
    package_data={"htdocs": [f[len("htdocs/") :] for f in glob("htdocs/**/*", recursive=True)]},
    entry_points={"console_scripts": ["openwebrx=owrx.__main__:main"]},
    url="https://www.openwebrx.de/",
    author="Jakob Ketterl",
    author_email="jakob.ketterl@gmx.de",
    maintainer="Jakob Ketterl",
    maintainer_email="jakob.ketterl@gmx.de",
    license="GAGPL",
    python_requires=">=3.5",
)
