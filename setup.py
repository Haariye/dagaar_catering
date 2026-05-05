from setuptools import setup, find_packages

with open("requirements.txt") as f:
	install_requires = f.read().strip().split("\n")

from dagaar_catering import __version__ as version

setup(
	name="dagaar_catering",
	version=version,
	description="DagaarSoft Catering - Enterprise Catering Management System",
	author="DagaarSoft",
	author_email="support@dagaarsoft.com",
	packages=find_packages(),
	zip_safe=False,
	include_package_data=True,
	install_requires=install_requires,
)
