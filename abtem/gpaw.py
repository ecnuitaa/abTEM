"""Module to handle ab initio electrostatic potentials from the DFT code GPAW."""
import contextlib
import copy
import os
from collections import defaultdict
from dataclasses import dataclass
from functools import partial
from math import pi
from typing import Any
from typing import TYPE_CHECKING
from typing import Tuple, Union, List

import dask
import dask.array as da
import numpy as np
from ase import Atoms
from ase import units
from ase.data import chemical_symbols, atomic_numbers
from ase.io.trajectory import read_atoms
from ase.units import Bohr
from scipy.interpolate import interp1d

from abtem.charge_density import _generate_slices
from abtem.core.axes import AxisMetadata
from abtem.core.constants import eps0
from abtem.core.electron_configurations import (
    electron_configurations,
    config_str_to_config_tuples,
)
from abtem.core.parametrizations.ewald import EwaldParametrization
from abtem.inelastic.phonons import (
    DummyFrozenPhonons,
    FrozenPhonons,
    AbstractFrozenPhonons,
)
from abtem.potentials import _PotentialBuilder, Potential

try:
    from gpaw import GPAW
    from gpaw.lfc import LFC, BasisFunctions
    from gpaw.transformers import Transformer
    from gpaw.utilities import unpack2
    from gpaw import GPAW
    from gpaw.atom.aeatom import AllElectronAtom
    from gpaw.io import Reader
    from gpaw.density import RealSpaceDensity
    from gpaw.mpi import SerialCommunicator
    from gpaw.grid_descriptor import GridDescriptor
    from gpaw.utilities import unpack_atomic_matrices

    if TYPE_CHECKING:
        from gpaw.setup import Setups
except:
    GPAW = None
    LFC = None
    BasisFunctions = None
    Transformer = None
    unpack2 = None
    Setups = None
    AllElectronAtom = None
    Reader = None
    SerialCommunicator = None
    GridDescriptor = None
    unpack_atomic_matrices = None


def _safe_read_atoms(calculator, clean: bool = True):
    if isinstance(calculator, str):
        atoms = read_atoms(Reader(calculator).atoms)
    else:
        atoms = calculator.atoms

    if clean:
        atoms.constraints = None
        atoms.calc = True

    return atoms


def _get_gpaw_setups(atoms, mode, xc):
    gpaw = GPAW(txt=None, mode=mode, xc=xc)
    gpaw.initialize(atoms)
    return gpaw.setups


@dataclass
class _DummyGPAW:
    setup_mode: str
    setup_xc: str
    nt_sG: np.ndarray
    gd: Any
    D_asp: np.ndarray
    atoms: Atoms

    @property
    def setups(self):
        gpaw = GPAW(txt=None, mode=self.setup_mode, xc=self.setup_xc)
        gpaw.initialize(self.atoms)
        return gpaw.setups

    @classmethod
    def from_gpaw(cls, gpaw, lazy: bool = True):
        # if lazy:
        #    return dask.delayed(cls.from_gpaw)(gpaw, lazy=False)

        atoms = gpaw.atoms.copy()
        atoms.calc = None

        kwargs = {
            "setup_mode": gpaw.parameters["mode"],
            "setup_xc": gpaw.parameters["xc"],
            "nt_sG": gpaw.density.nt_sG.copy(),
            "gd": gpaw.density.gd.new_descriptor(comm=SerialCommunicator()),
            "D_asp": dict(gpaw.density.D_asp),
            "atoms": atoms,
        }
        return cls(**kwargs)

    @classmethod
    def from_file(cls, path: str, lazy: bool = True):
        if lazy:
            return dask.delayed(cls.from_file)(path, lazy=False)

        reader = Reader(path)
        atoms = read_atoms(reader.atoms)

        from gpaw.calculator import GPAW

        parameters = copy.copy(GPAW.default_parameters)
        parameters.update(reader.parameters.asdict())

        setup_mode = parameters["mode"]
        setup_xc = parameters["xc"]

        if isinstance(setup_xc, dict) and "setup_name" in setup_xc:
            setup_xc = setup_xc["setup_name"]

        assert isinstance(setup_xc, str)

        density = reader.density.density * units.Bohr ** 3
        gd = GridDescriptor(
            N_c=density.shape[-3:],
            cell_cv=atoms.get_cell() / Bohr,
            comm=SerialCommunicator(),
        )

        setups = _get_gpaw_setups(atoms, setup_mode, setup_xc)

        D_asp = unpack_atomic_matrices(reader.density.atomic_density_matrices, setups)

        kwargs = {
            "setup_mode": setup_mode,
            "setup_xc": setup_xc,
            "nt_sG": density,
            "gd": gd,
            "D_asp": D_asp,
            "atoms": atoms,
        }
        return cls(**kwargs)

    @classmethod
    def from_generic(cls, calculator, lazy: bool = True):
        if isinstance(calculator, str):
            return cls.from_file(calculator, lazy=lazy)
        elif hasattr(calculator, "density"):
            return cls.from_gpaw(calculator, lazy=lazy)
        elif isinstance(calculator, cls):
            return calculator
        else:
            raise RuntimeError()


def _interpolate_pseudo_density(nt_sg, gd, gridrefinement=1):
    if gridrefinement == 1:
        return nt_sg, gd

    assert gridrefinement % 2 == 0

    iterations = int(np.log(gridrefinement) / np.log(2))

    finegd = gd
    n_sg = nt_sg

    for i in range(iterations):
        finegd = gd.refine()
        interpolator = Transformer(gd, finegd, 3)

        n_sg = finegd.empty(nt_sg.shape[0])

        for s in range(nt_sg.shape[0]):
            interpolator.apply(nt_sg[s], n_sg[s])

        nt_sg = n_sg
        gd = finegd

    return n_sg, finegd


def _get_all_electron_density(
    nt_sG, gd, D_asp: dict, setups, atoms: Atoms, gridrefinement: int = 1
):
    nspins = nt_sG.shape[0]
    spos_ac = atoms.get_scaled_positions() % 1.0

    n_sg, gd = _interpolate_pseudo_density(nt_sG, gd, gridrefinement)

    phi_aj = []
    phit_aj = []
    nc_a = []
    nct_a = []
    for setup in setups:
        phi_j, phit_j, nc, nct = setup.get_partial_waves()[:4]
        phi_aj.append(phi_j)
        phit_aj.append(phit_j)
        nc_a.append([nc])
        nct_a.append([nct])

    # Create localized functions from splines
    phi = BasisFunctions(gd, phi_aj)
    phit = BasisFunctions(gd, phit_aj)
    nc = LFC(gd, nc_a)
    nct = LFC(gd, nct_a)
    phi.set_positions(spos_ac)
    phit.set_positions(spos_ac)
    nc.set_positions(spos_ac)
    nct.set_positions(spos_ac)

    I_sa = np.zeros((nspins, len(spos_ac)))
    a_W = np.empty(len(phi.M_W), np.intc)
    W = 0
    for a in phi.atom_indices:
        nw = len(phi.sphere_a[a].M_w)
        a_W[W : W + nw] = a
        W += nw

    x_W = phi.create_displacement_arrays()[0]

    rho_MM = np.zeros((phi.Mmax, phi.Mmax))
    for s, I_a in enumerate(I_sa):
        M1 = 0
        for a, setup in enumerate(setups):
            ni = setup.ni
            D_sp = D_asp.get(a % len(D_asp))
            if D_sp is None:
                D_sp = np.empty((nspins, ni * (ni + 1) // 2))
            else:
                I_a[a] = setup.Nct / nspins - np.sqrt(4 * pi) * np.dot(
                    D_sp[s], setup.Delta_pL[:, 0]
                )
                I_a[a] -= setup.Nc / nspins

            # rank = D_asp.partition.rank_a[a]
            # D_asp.partition.comm.broadcast(D_sp, rank)
            M2 = M1 + ni
            rho_MM[M1:M2, M1:M2] = unpack2(D_sp[s])
            M1 = M2

        assert np.all(n_sg[s].shape == phi.gd.n_c)
        phi.lfc.ae_valence_density_correction(rho_MM, n_sg[s], a_W, I_a, x_W)
        phit.lfc.ae_valence_density_correction(-rho_MM, n_sg[s], a_W, I_a, x_W)

    a_W = np.empty(len(nc.M_W), np.intc)
    W = 0
    for a in nc.atom_indices:
        nw = len(nc.sphere_a[a].M_w)
        a_W[W : W + nw] = a
        W += nw
    scale = 1.0 / nspins

    for s, I_a in enumerate(I_sa):
        nc.lfc.ae_core_density_correction(scale, n_sg[s], a_W, I_a)
        nct.lfc.ae_core_density_correction(-scale, n_sg[s], a_W, I_a)
        # D_asp.partition.comm.sum(I_a)

        N_c = gd.N_c
        g_ac = np.around(N_c * spos_ac).astype(int) % N_c - gd.beg_c

        for I, g_c in zip(I_a, g_ac):
            if np.all(g_c >= 0) and np.all(g_c < gd.n_c):
                n_sg[s][tuple(g_c)] -= I / gd.dv

    return n_sg.sum(0) / Bohr ** 3


class GPAWPotential(_PotentialBuilder):
    def __init__(
        self,
        calculators: Union["GPAW", List["GPAW"], List[str], str],
        gpts: Union[int, Tuple[int, int]] = None,
        sampling: Union[float, Tuple[float, float]] = None,
        slice_thickness: float = 1.0,
        exit_planes: int = None,
        gridrefinement: int = 4,
        device: str = None,
        plane: str = "xy",
        box: Tuple[float, float, float] = None,
        origin: Tuple[float, float, float] = (0.0, 0.0, 0.0),
        periodic: bool = True,
        repetitions: Tuple[int, int, int] = (1, 1, 1),
        frozen_phonons: AbstractFrozenPhonons = None,
    ):
        """

        Calculate electrostatic potential from a converged DFT calculation using GPAW.

        Parameters
        ----------
        calculators : GPAW calculator or path to GPAW calculator or list of calculators or paths

        gpts : one or two int, optional
            Number of grid points in x and y describing each slice of the potential. Provide either "sampling" or "gpts".
        sampling : one or two float, optional
            Sampling of the potential in x and y [1 / Å]. Provide either "sampling" or "gpts".
        slice_thickness
        exit_planes
        gridrefinement
        device
        plane
        box
        origin
        periodic
        repetitions
        frozen_phonons
        """

        if GPAW is None:
            raise RuntimeError(
                "This functionality of abTEM requires GPAW, see https://wiki.fysik.dtu.dk/gpaw/."
            )

        if isinstance(calculators, (tuple, list)):
            atoms = _safe_read_atoms(calculators[0])

            num_configs = len(calculators)

            if frozen_phonons is not None:
                raise ValueError()

            calculators = [
                _DummyGPAW.from_generic(calculator) for calculator in calculators
            ]

            frozen_phonons = DummyFrozenPhonons(atoms, num_configs=num_configs)

        else:
            atoms = _safe_read_atoms(calculators)

            calculators = _DummyGPAW.from_generic(calculators)

            if frozen_phonons is None:
                frozen_phonons = DummyFrozenPhonons(atoms, num_configs=None)

        self._calculators = calculators
        self._frozen_phonons = frozen_phonons
        self._gridrefinement = gridrefinement
        self._repetitions = repetitions

        cell = frozen_phonons.atoms.cell * repetitions
        frozen_phonons.atoms.calc = None

        super().__init__(
            gpts=gpts,
            sampling=sampling,
            cell=cell,
            slice_thickness=slice_thickness,
            exit_planes=exit_planes,
            device=device,
            plane=plane,
            origin=origin,
            box=box,
            periodic=periodic,
        )

    @property
    def frozen_phonons(self):
        return self._frozen_phonons

    @property
    def repetitions(self):
        return self._repetitions

    @property
    def gridrefinement(self):
        return self._gridrefinement

    @property
    def calculators(self):
        return self._calculators

    def _get_all_electron_density(self):

        calculator = (
            self.calculators[0]
            if isinstance(self.calculators, list)
            else self.calculators
        )

        try:
            calculator = calculator.compute()
        except AttributeError:
            pass

        # assert len(self.calculators) == 1

        calculator = _DummyGPAW.from_generic(calculator)
        atoms = self.frozen_phonons.atoms

        if self.repetitions != (1, 1, 1):
            cell_cv = calculator.gd.cell_cv * self.repetitions
            N_c = tuple(
                n_c * rep for n_c, rep in zip(calculator.gd.N_c, self.repetitions)
            )
            gd = calculator.gd.new_descriptor(N_c=N_c, cell_cv=cell_cv)
            atoms = atoms * self.repetitions
            nt_sG = np.tile(calculator.nt_sG, self.repetitions)
        else:
            gd = calculator.gd
            nt_sG = calculator.nt_sG

        # gridrefinement = self.gridrefinement
        # if np.isscalar(self.gridrefinement):
        #    gridrefinement = (gridrefinement,) * 3

        # if not all([r == 1 for r in gridrefinement]):
        #    cell_cv = gd.cell_cv
        #    N_c = tuple(n_c * r for n_c, r in zip(gd.N_c, gridrefinement))
        #    nt_sG = fft_interpolate(nt_sG, new_shape=N_c).astype(np.float64)
        #    gd = gd.new_descriptor(N_c=N_c, cell_cv=cell_cv)

        random_atoms = self.frozen_phonons.randomize(atoms)

        gpaw = GPAW(txt=None, mode=calculator.setup_mode, xc=calculator.setup_xc)
        gpaw.initialize(random_atoms)

        return _get_all_electron_density(
            nt_sG=nt_sG,
            gd=gd,
            D_asp=calculator.D_asp,
            setups=gpaw.setups,
            gridrefinement=self.gridrefinement,
            atoms=random_atoms,
        )

    def generate_slices(self, first_slice: int = 0, last_slice: int = None):
        if last_slice is None:
            last_slice = len(self)

        atoms = self.frozen_phonons.atoms * self.repetitions
        random_atoms = self.frozen_phonons.randomize(atoms)

        ewald_parametrization = EwaldParametrization(width=1)

        ewald_potential = Potential(
            atoms=random_atoms,
            gpts=self.gpts,
            sampling=self.sampling,
            parametrization=ewald_parametrization,
            slice_thickness=self.slice_thickness,
            projection="finite",
            integral_method="quadrature",
            plane=self.plane,
            box=self.box,
            origin=self.origin,
            exit_planes=self.exit_planes,
            device=self.device,
        )

        array = self._get_all_electron_density()

        for slic in _generate_slices(
            array, ewald_potential, first_slice=first_slice, last_slice=last_slice
        ):
            yield slic

    @property
    def ensemble_axes_metadata(self) -> List[AxisMetadata]:
        return self._frozen_phonons.ensemble_axes_metadata

    @property
    def num_frozen_phonons(self):
        return len(self.calculators)

    @property
    def ensemble_shape(self):
        return self._frozen_phonons.ensemble_shape

    @staticmethod
    def _gpaw_potential(*args, frozen_phonons_partial, **kwargs):
        args = args[0]
        if hasattr(args, "item"):
            args = args.item()

        if args["frozen_phonons"] is not None:
            frozen_phonons = frozen_phonons_partial(args["frozen_phonons"])
        else:
            frozen_phonons = None

        calculators = args["calculators"]

        return GPAWPotential(calculators, frozen_phonons=frozen_phonons, **kwargs)

    def _from_partitioned_args(self):
        kwargs = self._copy_kwargs(exclude=("calculators", "frozen_phonons"))

        frozen_phonons_partial = self.frozen_phonons._from_partitioned_args()

        return partial(
            self._gpaw_potential,
            frozen_phonons_partial=frozen_phonons_partial,
            **kwargs
        )

    def _partition_args(self, chunks: int = 1, lazy: bool = True):

        chunks = self._validate_chunks(chunks)

        def frozen_phonons(calculators, frozen_phonons):
            arr = np.zeros((1,), dtype=object)
            arr.itemset(
                0, {"calculators": calculators, "frozen_phonons": frozen_phonons}
            )
            return arr

        calculators = self.calculators

        if isinstance(self.frozen_phonons, FrozenPhonons):
            array = np.zeros(len(self.frozen_phonons), dtype=object)
            for i, fp in enumerate(
                self.frozen_phonons._partition_args(chunks, lazy=lazy)[0]
            ):
                if lazy:
                    block = dask.delayed(frozen_phonons)(calculators, fp)

                    array.itemset(i, da.from_delayed(block, shape=(1,), dtype=object))
                else:
                    array.itemset(i, frozen_phonons(calculators, fp))

            if lazy:
                array = da.concatenate(list(array))

            return (array,)

        else:
            if len(self.ensemble_shape) == 0:
                array = np.zeros((1,), dtype=object)
                calculators = [calculators]
            else:
                array = np.zeros(self.ensemble_shape[0], dtype=object)

            for i, calculator in enumerate(calculators):

                if len(self.ensemble_shape) > 0:
                    calculator = [calculator]

                if lazy:
                    calculator = dask.delayed(calculator)
                    block = da.from_delayed(
                        dask.delayed(frozen_phonons)(calculator, None),
                        shape=(1,),
                        dtype=object,
                    )
                else:
                    block = frozen_phonons(calculator, None)

                array.itemset(i, block)

            if lazy:
                return (da.concatenate(list(array)),)
            else:
                return (array,)


class GPAWParametrization:
    def __init__(self):
        self._potential_functions = {}

    def _get_added_electrons(self, symbol, charge):
        if not charge:
            return []

        charge = np.sign(charge) * np.ceil(np.abs(charge))

        number = atomic_numbers[symbol]
        config = config_str_to_config_tuples(
            electron_configurations[chemical_symbols[number]]
        )
        ionic_config = config_str_to_config_tuples(
            electron_configurations[chemical_symbols[number - charge]]
        )

        config = defaultdict(lambda: 0, {shell[:2]: shell[2] for shell in config})
        ionic_config = defaultdict(
            lambda: 0, {shell[:2]: shell[2] for shell in ionic_config}
        )

        electrons = []
        for key in set(config.keys()).union(set(ionic_config.keys())):

            difference = config[key] - ionic_config[key]

            for i in range(np.abs(difference)):
                electrons.append(key + (np.sign(difference),))
        return electrons

    def _get_all_electron_atom(self, symbol, charge=0.0):

        with open(os.devnull, "w") as f, contextlib.redirect_stdout(f):
            ae = AllElectronAtom(symbol, spinpol=True, xc="PBE")

            added_electrons = self._get_added_electrons(symbol, charge)
            #     for added_electron in added_electrons:
            #         ae.add(*added_electron[:2], added_electron[-1])
            # # ae.run()
            # ae.run(mix=0.005, maxiter=5000, dnmax=1e-5)
            ae.run()
            ae.refine()

        return ae

        # vr_e = interp1d(radial_coord, electron_potential, fill_value='extrapolate', bounds_error=False)
        # vr = lambda r: atomic_numbers[symbol] / r / (4 * np.pi * eps0) + vr_e(r) / r * units.Hartree * units.Bohr

    def charge(self, symbol, charge=0.0):
        ae = self._get_all_electron_atom(symbol, charge)
        r = ae.rgd.r_g * units.Bohr
        n = ae.n_sg.sum(0) / units.Bohr ** 3
        return interp1d(r, n, fill_value="extrapolate", bounds_error=False)

    def potential(self, symbol, charge=0.0):
        ae = self._get_all_electron_atom(symbol, charge)
        r = ae.rgd.r_g * units.Bohr
        # n = ae.n_sg.sum(0) / units.Bohr ** 3

        ve = -ae.rgd.poisson(ae.n_sg.sum(0))
        ve = interp1d(r, ve, fill_value="extrapolate", bounds_error=False)
        # electron_potential = -ae.rgd.poisson(ae.n_sg.sum(0))

        vr = (
            lambda r: atomic_numbers[symbol] / r / (4 * np.pi * eps0)
            + ve(r) / r * units.Hartree * units.Bohr
        )
        return vr

    # def get_function(self, symbol, charge=0.):
    #     #if symbol in self._potential_functions.keys():
    #     #    return self._potential_functions[(symbol, charge)]
    #
    #
    #
    #     self._potential_functions[(symbol, charge)] = vr
    #     return self._potential_functions[(symbol, charge)]
    #
    # def potential(self, r, symbol, charge=0.):
    #     potential = self._calculate(symbol, charge)
    #     return potential(r)