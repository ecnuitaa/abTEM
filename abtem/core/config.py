import ast
import os
import sys
import threading
import warnings
from collections.abc import Mapping, Sequence
from typing import Any, Literal, Union

import yaml
from dask.config import deserialize, update, canonical_name, collect

no_default = "__no_default__"


def _get_paths():
    """Get locations to search for YAML configuration files.

    This logic exists as a separate function for testing purposes.
    """

    paths = [
        os.getenv("ABTEM_ROOT_CONFIG", "/etc/abtem"),
        os.path.join(sys.prefix, "etc", "abtem"),
        os.path.join(os.path.expanduser("~"), ".config", "abtem"),
        os.path.join(os.path.expanduser("~"), ".abtem"),
    ]

    if "ABTEM_CONFIG" in os.environ:
        paths.append(os.environ["ABTEM_CONFIG"])

    # Remove duplicate paths while preserving ordering
    paths = list(reversed(list(dict.fromkeys(reversed(paths)))))

    return paths


paths = _get_paths()

if "ABTEM_CONFIG" in os.environ:
    PATH = os.environ["ABTEM_CONFIG"]
else:
    PATH = os.path.join(os.path.expanduser("~"), ".config", "abtem")

config: dict = {}
global_config = config  # alias

config_lock = threading.Lock()

defaults: list[Mapping] = []


def _load_config_file(path: str) -> Union[dict, None]:
    """A helper for loading a config file from a path, and erroring
    appropriately if the file is malformed."""
    try:
        with open(path) as f:
            config = yaml.safe_load(f.read())
    except OSError:
        # Ignore permission errors
        return None
    except Exception as exc:
        raise ValueError(
            f"An abTEM config file at {path!r} is malformed, original error "
            f"message:\n\n{exc}"
        ) from None
    if config is not None and not isinstance(config, dict):
        raise ValueError(
            f"A abTEM config file at {path!r} is malformed - config files must have "
            f"a dict as the top level object, got a {type(config).__name__} instead"
        )
    return config


def collect_env(env: Union[Mapping[str, str], None] = None) -> dict:
    """Collect config from environment variables

    This grabs environment variables of the form "ABTEM_FOO__BAR_BAZ=123" and
    turns these into config variables of the form ``{"foo": {"bar-baz": 123}}``
    It transforms the key and value in the following way:

    -  Lower-cases the key text
    -  Treats ``__`` (double-underscore) as nested access
    -  Calls ``ast.literal_eval`` on the value

    Any serialized config passed via ``ABTEM_INTERNAL_INHERIT_CONFIG`` is also set here.

    """

    if env is None:
        env = os.environ

    if "ABTEM_INTERNAL_INHERIT_CONFIG" in env:
        d = deserialize(env["ABTEM_INTERNAL_INHERIT_CONFIG"])
    else:
        d = {}

    for name, value in env.items():
        if name.startswith("ABTEM_"):
            varname = name[5:].lower().replace("__", ".")
            try:
                d[varname] = ast.literal_eval(value)
            except (SyntaxError, ValueError):
                d[varname] = value

    result: dict = {}
    set(d, config=result)
    return result


class set:
    """Temporarily set configuration values within a context manager

    Parameters
    ----------
    arg : mapping or None, optional
        A mapping of configuration key-value pairs to set.
    **kwargs :
        Additional key-value pairs to set. If ``arg`` is provided, values set
        in ``arg`` will be applied before those in ``kwargs``.
        Double-underscores (``__``) in keyword arguments will be replaced with
        ``.``, allowing nested values to be easily set.

    Examples
    --------
    >>> import dask

    Set ``'foo.bar'`` in a context, by providing a mapping.

    >>> with dask.config.set({'foo.bar': 123}):
    ...     pass

    Set ``'foo.bar'`` in a context, by providing a keyword argument.

    >>> with dask.config.set(foo__bar=123):
    ...     pass

    Set ``'foo.bar'`` globally.

    >>> dask.config.set(foo__bar=123)  # doctest: +SKIP

    See Also
    --------
    dask.config.get
    """

    config: dict
    # [(op, path, value), ...]
    _record: list[tuple[Literal["insert", "replace"], tuple[str, ...], Any]]

    def __init__(
            self,
            arg: Union[Mapping, None] = None,
            config: dict = config,
            lock: threading.Lock = config_lock,
            **kwargs,
    ):
        with lock:
            self.config = config
            self._record = []

            if arg is not None:
                for key, value in arg.items():
                    key = check_deprecations(key)
                    self._assign(key.split("."), value, config)
            if kwargs:
                for key, value in kwargs.items():
                    key = key.replace("__", ".")
                    key = check_deprecations(key)
                    self._assign(key.split("."), value, config)

    def __enter__(self):
        return self.config

    def __exit__(self, type, value, traceback):
        for op, path, value in reversed(self._record):
            d = self.config
            if op == "replace":
                for key in path[:-1]:
                    d = d.setdefault(key, {})
                d[path[-1]] = value
            else:  # insert
                for key in path[:-1]:
                    try:
                        d = d[key]
                    except KeyError:
                        break
                else:
                    d.pop(path[-1], None)

    def _assign(
            self,
            keys: Sequence[str],
            value: Any,
            d: dict,
            path: tuple[str, ...] = (),
            record: bool = True,
    ) -> None:
        """Assign value into a nested configuration dictionary

        Parameters
        ----------
        keys : Sequence[str]
            The nested path of keys to assign the value.
        value : object
        d : dict
            The part of the nested dictionary into which we want to assign the
            value
        path : tuple[str], optional
            The path history up to this point.
        record : bool, optional
            Whether this operation needs to be recorded to allow for rollback.
        """
        key = canonical_name(keys[0], d)

        path = path + (key,)

        if len(keys) == 1:
            if record:
                if key in d:
                    self._record.append(("replace", path, d[key]))
                else:
                    self._record.append(("insert", path, None))
            d[key] = value
        else:
            if key not in d:
                if record:
                    self._record.append(("insert", path, None))
                d[key] = {}
                # No need to record subsequent operations after an insert
                record = False
            self._assign(keys[1:], value, d[key], path, record=record)


def refresh(
        config: dict = config, defaults: list[Mapping] = defaults, **kwargs
) -> None:
    """
    Update configuration by re-reading yaml files and env variables

    This mutates the global dask.config.config, or the config parameter if
    passed in.

    This goes through the following stages:

    1.  Clearing out all old configuration
    2.  Updating from the stored defaults from downstream libraries
        (see update_defaults)
    3.  Updating from yaml files and environment variables

    Note that some functionality only checks configuration once at startup and
    may not change behavior, even if configuration changes.  It is recommended
    to restart your python process if convenient to ensure that new
    configuration changes take place.

    See Also
    --------
    dask.config.collect: for parameters
    dask.config.update_defaults
    """
    config.clear()

    for d in defaults:
        update(config, d, priority="old")

    update(config, collect(**kwargs))


def get(
        key: str,
        default: Any = no_default,
        config: dict = config,
        override_with: Any = None,
) -> Any:
    """
    Get elements from global config

    If ``override_with`` is not None this value will be passed straight back.
    Useful for getting kwarg defaults from Dask config.

    Use '.' for nested access

    Examples
    --------
    >>> from dask import config
    >>> config.get('foo')  # doctest: +SKIP
    {'x': 1, 'y': 2}

    >>> config.get('foo.x')  # doctest: +SKIP
    1

    >>> config.get('foo.x.y', default=123)  # doctest: +SKIP
    123

    >>> config.get('foo.y', override_with=None)  # doctest: +SKIP
    2

    >>> config.get('foo.y', override_with=3)  # doctest: +SKIP
    3

    See Also
    --------
    dask.config.set
    """
    if override_with is not None:
        return override_with
    keys = key.split(".")
    result = config
    for k in keys:
        k = canonical_name(k, result)
        try:
            result = result[k]
        except (TypeError, IndexError, KeyError):
            if default is not no_default:
                return default
            else:
                raise
    return result


def rename(aliases: Mapping, config: dict = config) -> None:
    """Rename old keys to new keys

    This helps migrate older configuration versions over time
    """
    old = []
    new = {}
    for o, n in aliases.items():
        value = get(o, None, config=config)
        if value is not None:
            old.append(o)
            new[n] = value

    for k in old:
        del config[canonical_name(k, config)]  # TODO: support nested keys

    set(new, config=config)


def update_defaults(
        new: Mapping, config: dict = config, defaults: list[Mapping] = defaults
) -> None:
    """Add a new set of defaults to the configuration

    It does two things:

    1.  Add the defaults to a global collection to be used by refresh later
    2.  Updates the global config with the new configuration
        prioritizing older values over newer ones
    """
    defaults.append(new)
    update(config, new, priority="old")


deprecations = {}


def check_deprecations(key: str, deprecations: dict = deprecations) -> str:
    """Check if the provided value has been renamed or removed

    Parameters
    ----------
    key : str
        The configuration key to check
    deprecations : Dict[str, str]
        The mapping of aliases

    Examples
    --------
    >>> deprecations = {"old_key": "new_key", "invalid": None}
    >>> check_deprecations("old_key", deprecations=deprecations)  # doctest: +SKIP
    UserWarning: Configuration key "old_key" has been deprecated. Please use "new_key" instead.

    >>> check_deprecations("invalid", deprecations=deprecations)
    Traceback (most recent call last):
        ...
    ValueError: Configuration value "invalid" has been removed

    >>> check_deprecations("another_key", deprecations=deprecations)
    'another_key'

    Returns
    -------
    new: str
        The proper key, whether the original (if no deprecation) or the aliased
        value
    """
    if key in deprecations:
        new = deprecations[key]
        if new:
            warnings.warn(
                'Configuration key "{}" has been deprecated. '
                'Please use "{}" instead'.format(key, new)
            )
            return new
        else:
            raise ValueError(f'Configuration value "{key}" has been removed')
    else:
        return key


def _initialize() -> None:
    fn = os.path.join(os.path.dirname(__file__), "abtem.yaml")

    with open(fn) as f:
        _defaults = yaml.safe_load(f)

    update_defaults(_defaults)


refresh()
_initialize()
