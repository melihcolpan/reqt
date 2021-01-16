#!/usr/bin/python
# -*- coding: utf-8 -*-

from setuptools import setup, find_packages


with open('README.md') as f:
    readme = f.read()

with open('LICENSE') as f:
    license = f.read()

setup(
    name='reqt',
    version='1.0.2',
    description='Reqt Package',
    long_description=readme,
    author='Melih Colpan',
    author_email='melihcolpan1@gmail.com',
    url='https://github.com/melihcolpan/reqt',
    license=license,
    install_requires=[
       'aiohttp'
    ],
    packages=find_packages(exclude=('tests', 'docs'))
)
