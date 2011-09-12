#!/usr/bin/python

from distutils.core import setup

setup(name='git-core-slug',
      version='0.001',
      description='Scripts to interact with PLD git repos',
      author='Kacper Kornet',
      author_email='draenog@pld-linux.org',
      url='https://github.com/draenog/slug',
      packages=['git_slug'],
      scripts=['slug.py']
     )
