#!/usr/bin/python
# -*- coding: utf-8 -*-

from setuptools import setup, find_packages


with open('README.rst') as f:
    readme = f.read()

with open('LICENSE') as f:
    license = f.read()

setup(
    name='requestor',
    version='1.0.0',
    description='Requestor Package',
    long_description=readme,
    author='Melih Colpan',
    author_email='melihcolpan1@gmail.com',
    url='https://github.com/melihcolpan',
    license=license,
    packages=find_packages(exclude=('tests', 'docs'))
)
