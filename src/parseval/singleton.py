from __future__ import annotations

from threading import Lock


def singleton(cls):
    """
    A decorator to make a class a singleton.

    This ensures that only one instance of the class is created, even in a multithreaded environment.

    Args:
        cls: The class to be made a singleton.

    Returns:
        A wrapper function that ensures only one instance of the class is created.
    """

    instance = {}
    _lock: Lock = Lock()

    def _singleton(*args, **kwargs):
        """
        Wrapper function to create or retrieve the singleton instance.

        Args:
            *args: Positional arguments to pass to the class constructor.
            **kwargs: Keyword arguments to pass to the class constructor.

        Returns:
            The singleton instance of the class.
        """
        with _lock:
            if cls not in instance:
                instance[cls] = cls(*args, **kwargs)
            return instance[cls]

    return _singleton


class singletonMeta(type):
    """
    A thread-safe implementation of the Singleton pattern using a metaclass.

    This metaclass ensures that only one instance of a class is created,
    even in a multithreaded environment. Classes that use this metaclass
    will automatically follow the Singleton pattern.

    Attributes:
        _instances (dict): A dictionary to store the single instance of each class.
        _lock (Lock): A threading lock to ensure thread safety during instance creation.
    """

    _instances = {}
    _lock: Lock = Lock()

    def __call__(cls, *args, **kwargs):
        """
        Create or retrieve the singleton instance of the class.

        This method overrides the default `__call__` method to ensure that
        only one instance of the class is created.

        Args:
            *args: Positional arguments to pass to the class constructor.
            **kwargs: Keyword arguments to pass to the class constructor.

        Returns:
            The singleton instance of the class.
        """
        with cls._lock:
            if cls not in cls._instances:
                instance = super().__call__(*args, **kwargs)
                cls._instances[cls] = instance
        return cls._instances[cls]
