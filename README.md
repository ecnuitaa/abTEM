:warning: **Major update** :warning: Warning: the recent update to the main branch marks a major change in *ab*TEM which may break some code, this change will come in full effect on the next release. 

You can install the current main branch using `pip install git+https://github.com/abtem/abtem` and check the new [documentation](https://abtem.github.io/doc/intro.html). After the next release you can still install the old one by visiting this [repository](https://github.com/abTEM/abTEM-legacy).

# *ab*TEM: transmission electron microscopy from first principles

[![PyPI version](https://badge.fury.io/py/abtem.svg)](https://badge.fury.io/py/abtem)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
[![Documentation Status](https://readthedocs.org/projects/abtem/badge/?version=latest)](https://abtem.readthedocs.io/en/latest/?badge=latest)
[![Binder](https://mybinder.org/badge_logo.svg)](https://mybinder.org/v2/gh/jacobjma/abTEM/master?filepath=examples%2Findex.ipynb)
[![DOI](https://zenodo.org/badge/205110910.svg)](https://zenodo.org/badge/latestdoi/205110910)

[**Docs**](https://abtem.readthedocs.io/en/latest/index.html)
| [**Install Guide**](https://abtem.readthedocs.io/en/latest/install.html)
| [**Examples**](https://github.com/jacobjma/abTEM/tree/master/examples)

*ab*TEM (pronounced "ab-tem", as in "*ab initio*") provides a Python API for running simulations of (scanning)
transmission electron microscopy images and diffraction patterns using the multislice or PRISM algorithms. It is
designed to closely integrate with atomistic simulations using the Atomic Simulation
Environment ([ASE](https://wiki.fysik.dtu.dk/ase/)), and to directly use *ab initio* electrostatic potentials from the
high-performance density functional theory code [GPAW](https://wiki.fysik.dtu.dk/gpaw/). *ab*TEM is open source, purely
written in Python, very fast, and extremely versatile and easy to extend.

## Installation

You can install *ab*TEM using `pip`:

```sh
$ pip install abtem
```

For detailed instructions on installing *ab*TEM,
see [the installation guide](https://abtem.readthedocs.io/en/latest/install.html).

## Getting started

To get started using *ab*TEM, please visit
our [walkthrough](https://abtem.readthedocs.io/en/latest/walkthrough/introduction.html) or check out one of
the [examples](https://github.com/jacobjma/abTEM/tree/master/examples).

To try *ab*TEM in your web browser, please click on the following Binder link:

[![Binder](https://mybinder.org/badge_logo.svg)](https://mybinder.org/v2/gh/jacobjma/abTEM/master?filepath=examples%2Findex.ipynb)

## Citing abTEM

If you find *ab*TEM useful in your research, please cite our methods article:

J. Madsen & T. Susi, "The abTEM code: transmission electron microscopy from first principles", Open Research Europe 1:
24 (2021), doi:[10.12688/openreseurope.13015.1](https://doi.org/10.12688/openreseurope.13015.1).

Open code from articles using *ab*TEM is available in
our [repository](https://github.com/jacobjma/abTEM/tree/master/articles).

## Contact

* Write the [maintainer](https://github.com/jacobjma) directly
* Bug reports and development: [GitHub issues](https://github.com/jacobjma/abTEM/issues)

Please send us bug reports, patches, code, ideas and questions.
