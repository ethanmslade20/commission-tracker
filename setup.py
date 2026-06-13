from setuptools import setup, find_packages

setup(
    name="commission-tracker",
    version="0.1.0",
    packages=find_packages(),
    install_requires=[
        "click>=8.1",
        "gspread>=6.0",
        "google-auth>=2.28",
        "pandas>=2.2",
        "pyyaml>=6.0",
        "openpyxl>=3.1",
        "tabulate>=0.9",
        "pyarrow>=15.0",
    ],
    entry_points={
        "console_scripts": [
            "track=tracker.cli:cli",
        ],
    },
)
