from setuptools import setup, find_packages
from os import path

here = path.abspath(path.dirname(__file__))

setup(
    name='order_trees',
    version="0.0.1",
    description='Cached order book data structure.',
    url='https://github.com/humdings/order_trees',
    author='David Edwards',
    author_email='humdings@gmail.com',
    # license='MIT',
    classifiers=[
        'Operating System :: OS Independent',
        'Intended Audience :: Developers',
        'Topic :: Software Development :: Libraries :: Python Modules',
        'Programming Language :: Python :: 3',
    ],
    platforms=['any'],
    packages=find_packages(exclude=['contrib', 'docs', 'tests', 'examples']),
)
