from __future__ import annotations

from typing import Any, TypeVar, Set, Mapping, Hashable, Iterable, Iterator

K = TypeVar("K", bound=Hashable)


class DisjointSet(Mapping[K, Set[K]]):
    """Disjoint set data structure.
    From  https://github.com/ibis-project/ibis/blob/main/ibis/common/egraph.py
    Also known as union-find data structure. It is a data structure that keeps
    track of a set of elements partitioned into a number of disjoint (non-overlapping)
    subsets. It provides near-constant-time operations to add new sets, to merge
    existing sets, and to determine whether elements are in the same set.
    """

    __slots__ = ("_parents", "_classes")
    _parents: dict
    _classes: dict

    def __init__(self, data: Iterable[K] | None = None):
        self._parents = {}
        self._classes = {}
        if data is not None:
            for key in data:
                self.add(key)


    def __contains__(self, key) -> bool:
        """Check if the given id is in the disjoint set.

        Parameters
        ----------
        id :
            The id to check.

        Returns
        -------
        ined:
            True if the id is in the disjoint set, False otherwise.

        """
        return key in self._parents
    
    def __getitem__(self, key) -> Set[K]:
        """Get the set of ids that are in the same class as the given id.

        Parameters
        ----------
        id :
            The id to get the class for.

        Returns
        -------
        class:
            The set of ids that are in the same class as the given id, including
            the given id.

        """
        key = self._parents[key]
        return self._classes[key]

    def __iter__(self) -> Iterator[K]:
        """Iterate over the ids in the disjoint set."""
        return iter(self._parents)

    def __len__(self) -> int:
        """Get the number of ids in the disjoint set."""
        return len(self._parents)

    def __eq__(self, other: object) -> bool:
        """Check if the disjoint set is equal to another disjoint set.

        Parameters
        ----------
        other :
            The other disjoint set to compare to.

        Returns
        -------
        equal:
            True if the disjoint sets are equal, False otherwise.

        """
        if not isinstance(other, DisjointSet):
            return NotImplemented
        return self._parents == other._parents

    def copy(self) -> DisjointSet:
        """Make a copy of the disjoint set.

        Returns
        -------
        copy:
            A copy of the disjoint set.

        """
        ds = DisjointSet()
        ds._parents = self._parents.copy()
        ds._classes = self._classes.copy()
        return ds

    def add(self, key: K) -> K:
        """Add a new id to the disjoint set.

        If the id is not in the disjoint set, it will be added to the disjoint set
        along with a new class containing only the given id.

        Parameters
        ----------
        id :
            The id to add to the disjoint set.

        Returns
        -------
        id:
            The id that was added to the disjoint set.

        """
        if key in self._parents:
            return self._parents[key]
        self._parents[key] = key
        self._classes[key] = {key}
        return key

    def find(self, key: K) -> K:
        """Find the root of the class that the given id is in.

        Also called as the canonicalized id or the representative id.

        Parameters
        ----------
        id :
            The id to find the canonicalized id for.

        Returns
        -------
        id:
            The canonicalized id for the given id.

        """
        return self._parents[key]

    def union(self, key1, key2) -> bool:
        """Merge the classes that the given ids are in.

        If the ids are already in the same class, this will return False. Otherwise
        it will merge the classes and return True.

        Parameters
        ----------
        key1 :
            The first id to merge the classes for.
        key2 :
            The second id to merge the classes for.

        Returns
        -------
        merged:
            True if the classes were merged, False otherwise.

        """
        # Find the root of each class
        key1 = self._parents[key1]
        key2 = self._parents[key2]
        if hash(key1) == hash(key2):
            return False

        # Merge the smaller eclass into the larger one, aka. union-find by size
        class1 = self._classes[key1]
        class2 = self._classes[key2]
        if len(class1) >= len(class2):
            key1, key2 = key2, key1
            class1, class2 = class2, class1

        # Update the parent pointers, this is called path compression but done
        # during the union operation to keep the find operation minimal
        for key in class1:
            self._parents[key] = key2

        # Do the actual merging and clear the other eclass
        class2 |= class1
        class1.clear()

        return True

    def connected(self, key1, key2):
        """Check if the given ids are in the same class.

        True if both ids have the same canonicalized id, False otherwise.

        Parameters
        ----------
        key1 :
            The first id to check.
        key2 :
            The second id to check.

        Returns
        -------
        connected:
            True if the ids are connected, False otherwise.

        """
        return hash(self._parents[key1]) == hash(self._parents[key2])

    def verify(self):
        """Verify that the disjoint set is not corrupted.

        Check that each id's canonicalized id's class. In general corruption
        should not happen if the public API is used, but this is a sanity check
        to make sure that the internal data structures are not corrupted.

        Returns
        -------
        verified:
            True if the disjoint set is not corrupted, False otherwise.

        """
        for key in self._parents:
            if key not in self._classes[self._parents[key]]:
                raise RuntimeError(
                    f"DisjointSet is corrupted: {key} is not in its class"
                )