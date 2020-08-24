"""Module for often-used base classes."""
from collections import OrderedDict
from copy import copy
from typing import Optional, Union, Sequence, Any, Callable

import numpy as np

from abtem.utils import energy2wavelength, energy2sigma


class Event(object):
    """
    Event class for registering callbacks.
    """

    def __init__(self):
        self.callbacks = []
        self._notify_count = 0

    @property
    def notify_count(self):
        """
        Number of times this event has been notified.
        """
        return self._notify_count

    def notify(self, *args, **kwargs):
        """
        Notify this event. All registered callbacks are called.
        """
        self._notify_count += 1
        for callback in self.callbacks:
            callback(*args, **kwargs)

    def register(self, callbacks: Union[Callable, Sequence[Callable]]):
        """
        Register new callbacks.

        Parameters
        ----------
        callbacks : callable
            The callbacks to register.
        """
        if not isinstance(callbacks, list):
            callbacks = [callbacks]
        self.callbacks += callbacks


def watched_method(event: 'str'):
    """
    Decorator for class methods that have to notify.

    Parameters
    ----------
    event : str
        Name class property with target event.
    """

    def wrapper(func):
        property_name = func.__name__

        def new_func(*args, **kwargs):
            instance = args[0]
            result = func(*args, **kwargs)
            getattr(instance, event).notify(**{'notifier': instance, 'property_name': property_name, 'change': True})
            return result

        return new_func

    return wrapper


def watched_property(event: 'str'):
    """
    Decorator for class properties that have to notify an event.

    Parameters
    ----------
    event : str
        Name class property with target event
    """

    def wrapper(func):
        property_name = func.__name__

        def new_func(*args):
            instance, value = args
            old = getattr(instance, property_name)
            result = func(*args)
            change = old != value
            change = np.any(change)
            getattr(instance, event).notify(**{'notifier': instance, 'property_name': property_name, 'change': change})
            return result

        return new_func

    return wrapper


def cache_clear_callback(target_cache: 'Cache'):
    """
    Helper function for creating a callback that clears a target cache object.

    Parameters
    ----------
    target_cache : Cache object
        The target cache object.
    """

    def callback(notifier: Any, property_name: str, change: bool):
        if change:
            target_cache.clear()

    return callback


def cached_method(target_cache_property: str):
    """
    Decorator for cached methods. The method will store the output in the cache held by the target property.

    Parameters
    ----------
    target_cache_property : str
        The property holding the target cache.
    """

    def wrapper(func):

        def new_func(*args):
            cache = getattr(args[0], target_cache_property)
            key = (func,) + args[1:]

            if key in cache.cached:
                # The decorated method has been called once with the given args.
                # The calculation will be retrieved from cache.

                result = cache.retrieve(key)
                cache._hits += 1
            else:
                # The method will be called and its output will be cached.

                result = func(*args)
                cache.insert(key, result)
                cache._misses += 1

            return result

        return new_func

    return wrapper


class Cache:
    """
    Cache object.

    Simple class for handling a dictionary-based cache. When the cache is full, the first inserted item is deleted.

    Parameters
    ----------
    max_size : int
        The maximum number of values stored by this cache.
    """

    def __init__(self, max_size: int):
        self._max_size = max_size
        self._cached = OrderedDict()
        self._hits = 0
        self._misses = 0

    @property
    def cached(self) -> dict:
        """
        Dictionary of cached data.
        """
        return self._cached

    @property
    def hits(self) -> int:
        """
        Number of times a previously calculated object was retrieved.
        """
        return self._hits

    @property
    def misses(self) -> int:
        """
        Number of times a new object had to be calculated.
        """
        return self._hits

    def __len__(self) -> int:
        """
        Number of objects cached.
        """
        return len(self._cached)

    def insert(self, key: Any, value: Any):
        """
        Insert new value into the cache.

        Parameters
        ----------
        key : Any
            The dictionary key of the cached object.
        value : Any
            The object to cache.
        """
        self._cached[key] = value
        self._check_size()

    def retrieve(self, key: Any) -> Any:
        """
        Retrieve object from cache.

        Parameters
        ----------
        key: Any
            The key of the cached item.

        Returns
        -------
        Any
            The cached object.
        """
        return self._cached[key]

    def _check_size(self):
        """
        Delete item from cache, if it is too large.
        """
        if self._max_size is not None:
            while len(self) > self._max_size:
                self._cached.popitem(last=False)

    def clear(self):
        """
        Clear the cache.
        """
        self._cached = OrderedDict()
        self._hits = 0
        self._misses = 0


class Grid:
    """
    Grid object.

    The grid object represent the simulation grid on which the wave functions and potential are discretized.

    Parameters
    ----------
    extent : two float
        Grid extent in each dimension [Å].
    gpts : two int
        Number of grid points in each dimension.
    sampling : two float
        Grid sampling in each dimension [1 / Å].
    dimensions : int
        Number of dimensions represented by the grid.
    endpoint : bool
        If true include the grid endpoint (the default is False). For periodic grids the endpoint should not be included.
    """

    def __init__(self,
                 extent: Union[float, Sequence[float]] = None,
                 gpts: Union[int, Sequence[int]] = None,
                 sampling: Union[float, Sequence[float]] = None,
                 dimensions: int = 2,
                 endpoint: bool = False,
                 lock_extent=False,
                 lock_gpts=False,
                 lock_sampling=False):

        self.changed = Event()
        self._dimensions = dimensions
        self._endpoint = endpoint

        if sum([lock_extent, lock_gpts, lock_sampling]) > 1:
            raise RuntimeError('At most one of extent, gpts, and sampling may be locked')

        self._lock_extent = lock_extent
        self._lock_gpts = lock_gpts
        self._lock_sampling = lock_sampling

        self._extent = self._validate(extent, dtype=float)
        self._gpts = self._validate(gpts, dtype=int)
        self._sampling = self._validate(sampling, dtype=float)

        if self.extent is None:
            self._adjust_extent(self.gpts, self.sampling)

        if self.gpts is None:
            self._adjust_gpts(self.extent, self.sampling)

        self._adjust_sampling(self.extent, self.gpts)

    def _validate(self, value, dtype):
        if isinstance(value, (np.ndarray, list, tuple)):
            if len(value) != self.dimensions:
                raise RuntimeError('Grid value length of {} != {}'.format(len(value), self._dimensions))
            return tuple((map(dtype, value)))

        if isinstance(value, (int, float, complex)):
            return (dtype(value),) * self.dimensions

        if value is None:
            return value

        raise RuntimeError('Invalid grid property ({})'.format(value))

    def __len__(self) -> int:
        return self.dimensions

    @property
    def endpoint(self) -> bool:
        """
        Include the grid endpoint.
        """
        return self._endpoint

    @property
    def dimensions(self) -> int:
        """
        Number of dimensions represented by the grid.
        """
        return self._dimensions

    @property
    def extent(self) -> tuple:
        """
        Grid extent in each dimension [Å].
        """
        return self._extent

    @extent.setter
    @watched_method('changed')
    def extent(self, extent: Union[float, Sequence[float]]):
        if self._lock_extent:
            raise RuntimeError('Extent cannot be modified')

        extent = self._validate(extent, dtype=float)

        if self._lock_sampling or (self.gpts is None):
            self._adjust_gpts(extent, self.sampling)
            self._adjust_sampling(extent, self.gpts)
        elif self.gpts is not None:
            self._adjust_sampling(extent, self.gpts)

        self._extent = extent

    @property
    def gpts(self) -> tuple:
        """
        Number of grid points in each dimension.
        """
        return self._gpts

    @gpts.setter
    @watched_method('changed')
    def gpts(self, gpts: Union[int, Sequence[int]]):
        if self._lock_gpts:
            raise RuntimeError('Grid gpts cannot be modified')

        gpts = self._validate(gpts, dtype=int)

        if self._lock_sampling:
            self._adjust_extent(gpts, self.sampling)
        elif self.extent is not None:
            self._adjust_sampling(self.extent, gpts)
        else:
            self._adjust_extent(gpts, self.sampling)

        self._gpts = gpts

    @property
    def sampling(self) -> tuple:
        """
        Grid sampling in each dimension [1 / Å].
        """
        return self._sampling

    @sampling.setter
    @watched_method('changed')
    def sampling(self, sampling):
        if self._lock_sampling:
            raise RuntimeError('Sampling cannot be modified')

        sampling = self._validate(sampling, dtype=float)
        if self._lock_gpts:
            self._adjust_extent(self.gpts, sampling)
        elif self.extent is not None:
            self._adjust_gpts(self.extent, sampling)
        else:
            self._adjust_extent(self.gpts, sampling)

        self._adjust_sampling(self.extent, self.gpts)

    def _adjust_extent(self, gpts: tuple, sampling: tuple):
        if (gpts is not None) & (sampling is not None):
            if self._endpoint:
                self._extent = tuple((n - 1) * d for n, d in zip(gpts, sampling))
            else:
                self._extent = tuple(n * d for n, d in zip(gpts, sampling))

    def _adjust_gpts(self, extent: tuple, sampling: tuple):
        if (extent is not None) & (sampling is not None):
            if self._endpoint:
                self._gpts = tuple(int(np.ceil(l / d)) + 1 for l, d in zip(extent, sampling))
            else:
                self._gpts = tuple(int(np.ceil(l / d)) for l, d in zip(extent, sampling))

    def _adjust_sampling(self, extent: tuple, gpts: tuple):
        if (extent is not None) & (gpts is not None):
            if self._endpoint:
                self._sampling = tuple(l / (n - 1) for l, n in zip(extent, gpts))
            else:
                self._sampling = tuple(l / n for l, n in zip(extent, gpts))

    def check_is_defined(self):
        """
        Raise error if the grid is not defined.
        """

        if self.extent is None:
            raise RuntimeError('Grid extent is not defined')

        elif self.gpts is None:
            raise RuntimeError('Grid gpts are not defined')

    @property
    def antialiased_gpts(self) -> tuple:
        return tuple(n // 2 for n in self.gpts)

    @property
    def antialiased_sampling(self) -> tuple:
        return tuple(l / n for n, l in zip(self.antialiased_gpts, self.extent))

    def match(self, other: Union['Grid', 'HasGridMixin'], check_match: bool = False):
        """
        Set the parameters of this grid to match another grid.

        Parameters
        ----------
        other : Grid object
            The grid that should be matched.
        check_match : bool
            If true check whether grids can match without overriding already defined grid parameters.
        """

        if check_match:
            self.check_match(other)

        if (self.extent is None) & (other.extent is None):
            raise RuntimeError('Grid extent cannot be inferred')
        elif other.extent is None:
            other.extent = self.extent
        elif np.any(self.extent != other.extent):
            self.extent = other.extent

        if (self.gpts is None) & (other.gpts is None):
            raise RuntimeError('Grid gpts cannot be inferred')
        elif other.gpts is None:
            other.gpts = self.gpts
        elif np.any(self.gpts != other.gpts):
            self.gpts = other.gpts

    def check_match(self, other):
        """
        Raise error if the grid of another object is different from this object.

        Parameters
        ----------
        other : Grid object
            The grid that should be checked.
        """

        if (self.extent is not None) & (other.extent is not None) & np.any(self.extent != other.extent):
            raise RuntimeError('Inconsistent grid extent ({} != {})'.format(self.extent, other.extent))

        elif (self.gpts is not None) & (other.gpts is not None) & np.any(self.gpts != other.gpts):
            raise RuntimeError('Inconsistent grid gpts ({} != {})'.format(self.gpts, other.gpts))

    def snap_to_power(self, n: int = 2):
        """
        Round the grid gpts up to the nearest value that is a power of n. Fourier transforms are faster for arrays of
        whose size can be factored into small primes (2, 3, 5 and 7).

        Parameters
        ----------
        n : int

        """
        self.gpts = tuple(n ** np.ceil(np.log(n) / np.log(n)) for n in self.gpts)

    def __copy__(self):
        return self.__class__(extent=self.extent,
                              gpts=self.gpts,
                              sampling=self.sampling,
                              dimensions=self.dimensions,
                              endpoint=self.endpoint,
                              lock_extent=self._lock_extent,
                              lock_gpts=self._lock_gpts,
                              lock_sampling=self._lock_sampling)

    def copy(self):
        """
        Make a copy.
        """
        return copy(self)


class DelegatedAttribute:

    def __init__(self, delegate_name, attr_name):
        self.attr_name = attr_name
        self.delegate_name = delegate_name

    def __get__(self, instance, owner):
        if instance is None:
            return self
        else:
            return getattr(self.delegate(instance), self.attr_name)

    def __set__(self, instance, value):
        setattr(self.delegate(instance), self.attr_name, value)

    def __delete__(self, instance):
        delattr(self.delegate(instance), self.attr_name)

    def delegate(self, instance):
        return getattr(instance, self.delegate_name)


class HasGridMixin:
    _grid: Grid

    @property
    def grid(self) -> Grid:
        return self._grid

    extent = DelegatedAttribute('grid', 'extent')
    gpts = DelegatedAttribute('grid', 'gpts')
    sampling = DelegatedAttribute('grid', 'sampling')


class Accelerator:
    """
    Accelerator object describes the energy of wave functions and transfer functions.

    Parameters
    ----------
    energy: float
        Acceleration energy [eV].
    """

    def __init__(self, energy: Optional[float] = None, lock_energy=False):
        if energy is not None:
            energy = float(energy)

        self.changed = Event()
        self._energy = energy
        self._lock_energy = lock_energy

    @property
    def energy(self) -> float:
        """
        Acceleration energy [eV].
        """
        return self._energy

    @energy.setter
    @watched_method('changed')
    def energy(self, value: float):
        if self._lock_energy:
            raise RuntimeError('Energy cannot be modified')

        if value is not None:
            value = float(value)
        self._energy = value

    @property
    def wavelength(self) -> float:
        """
        Relativistic wavelength [Å].
        """
        self.check_is_defined()
        return energy2wavelength(self.energy)

    @property
    def sigma(self) -> float:
        """
        Interaction parameter.
        """
        self.check_is_defined()
        return energy2sigma(self.energy)

    def check_is_defined(self):
        """
        Raise error if the energy is not defined.
        """
        if self.energy is None:
            raise RuntimeError('Energy is not defined')

    def check_match(self, other: 'Accelerator'):
        """
        Raise error if the accelerator of another object is different from this object.

        Parameters
        ----------
        other: Accelerator object
            The accelerator that should be checked.
        """
        if (self.energy is not None) & (other.energy is not None) & (self.energy != other.energy):
            raise RuntimeError('Inconsistent energies')

    def match(self, other, check_match=False):
        """
        Set the parameters of this accelerator to match another accelerator.

        Parameters
        ----------
        other: Accelerator object
            The accelerator that should be matched.
        check_match: bool
            If true check whether accelerators can match without overriding an already defined energy.
        """

        if check_match:
            self.check_match(other)

        if (self.energy is None) & (other.energy is None):
            raise RuntimeError('Energy cannot be inferred')

        if other.energy is None:
            other.energy = self.energy

        else:
            self.energy = other.energy

    def __copy__(self):
        return self.__class__(self.energy)

    def copy(self):
        """
        Make a copy.
        """
        return copy(self)


class HasAcceleratorMixin:
    _accelerator: Accelerator

    @property
    def accelerator(self) -> Accelerator:
        return self._accelerator

    @accelerator.setter
    def accelerator(self, new: Accelerator):
        self._accelerator = new
        self._accelerator.changed = new.changed

    energy = DelegatedAttribute('accelerator', 'energy')
    wavelength = DelegatedAttribute('accelerator', 'wavelength')


class HasGridAndAcceleratorMixin(HasGridMixin, HasAcceleratorMixin):

    @property
    def max_scattering_angle(self):
        return 1 / np.max(self.grid.antialiased_sampling) * self.wavelength / 2 * 1000
