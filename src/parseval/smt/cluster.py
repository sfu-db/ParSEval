from __future__ import annotations


from collections import defaultdict, deque
import logging
from typing import Any, Dict, List, Set, Tuple, Optional
from src.parseval.plan.rex import Expression
from src.parseval import symbol as sym
from .domain import UnionFind


class CSPConstraint:
    def __init__(
        self,
        variables: List[sym.Variable],
        expression: Optional[Expression] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        self.expression = expression
        self.variables = variables
        self.metadata = metadata or {}

    def types(self):
        return set(v.dtype for v in self.variables)


class AllDifferenceConstraint(CSPConstraint):
    def __init__(
        self,
        variables: List[sym.Variable],
        expression: Optional[Expression] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(variables, expression, metadata)


class Graph:
    def __init__(
        self, var_to_constraints: Dict[str, Set], constraints: List[CSPConstraint]
    ):
        self.uf = UnionFind()
        self.constraints = constraints
        self.var_to_constraints = var_to_constraints

    def clusters_uf(self):
        for cset in self.var_to_constraints.values():
            indices = list(cset)
            for i in range(1, len(indices)):
                self.uf.union(indices[0], indices[i])

        groups = defaultdict(list)
        # for i in range(n):
        #     g

    def clusters(self):
        n = len(self.constraints)
        adj = [[] for _ in range(n)]
        for variable, cset in self.var_to_constraints.items():
            indices = list(cset)
            for i in range(len(indices)):
                for j in range(i + 1, len(indices)):
                    adj[indices[i]].append(indices[j])
                    adj[indices[j]].append(indices[i])
        visited = [False] * n
        components = []
        for i in range(n):
            if not visited[i]:
                q = deque([i])
                component = []
                visited[i] = True
                while q:
                    current = q.popleft()
                    component.append(self.constraints[current])
                    for neighbor in adj[current]:
                        if not visited[neighbor]:
                            visited[neighbor] = True
                            q.append(neighbor)
                components.append(component)
        return components
