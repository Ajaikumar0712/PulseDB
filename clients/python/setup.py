from setuptools import setup, find_packages

setup(
    name="pulsedb",
    version="1.0.0",
    description="Python client for PulseDB",
    long_description=open("README.md").read(),
    long_description_content_type="text/markdown",
    author="PulseDB Contributors",
    license="BUSL-1.1",
    packages=find_packages(),
    python_requires=">=3.8",
    classifiers=[
        "Programming Language :: Python :: 3",
        "Topic :: Database",
    ],
)
