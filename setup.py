from setuptools import setup, find_packages

with open("requirements.txt") as f:
    install_requires = f.read().strip().split("\n")

setup(
    name="tally_bridge",
    version="1.0.0",
    description="ERPNext to Tally Prime XML Export & Sync",
    author="Your Company",
    author_email="admin@example.com",
    packages=find_packages(),
    zip_safe=False,
    include_package_data=True,
    install_requires=install_requires,
)
