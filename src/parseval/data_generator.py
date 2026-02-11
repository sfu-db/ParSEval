from __future__ import annotations
from typing import Optional, List, Dict, TYPE_CHECKING, Tuple, Union, Set
from src.parseval.instance import Instance
from src.parseval.plan.encoder import PlanEncoder
from src.parseval.uexpr.uexprs import UExprToConstraint, Constraint, PBit
from src.parseval.constants import PlausibleType
from src.parseval.plan.rex import Symbol
from sqlglot.optimizer.scope import Scope
from sqlglot.optimizer.eliminate_joins import join_condition
from .solver.smt import SMTSolver
from .helper import convert_to_literal
from functools import reduce
from collections import deque
from src.parseval.faker.domain import UnionFind
from sqlglot import exp

import random, logging
if TYPE_CHECKING:
    from src.parseval.constants import PBit
    from src.parseval.uexpr.uexprs import PlausibleBranch
    

logger = logging.getLogger("parseval.coverage")

class DataGenerator:
    """
    Base class for data generators.
    """
    
    @staticmethod
    def set_random_seed(seed: int):
        random.seed(seed)
    
    def __init__(self, scope: Scope, instance: Instance, table_alias: Optional[Dict] = None, name: Optional[str] = None, workspace: str = None, verbose: bool = False, random_seed: int = 42):
        self.instance = instance
        self.name = name or instance.name
        self.scope: Scope = scope
        self.table_alias: Dict[str, str] = self._table_alias(table_alias)
        self.workspace = workspace
        self.constraints: Dict[str, List[exp.Expression]] = {} # label -> List[constraints]
        
        self.variables: Dict[Tuple[str, str], exp.Column] = {} # (columnref.table, columnef.name_{suffix}) -> variable
        self.var_to_columnref: Dict[Tuple[str, str], exp.Column] = {}   # columnref.table, columnref.name_{suffix} -> columnref
        self.table_to_vars: Dict[str, List[exp.Column]] = {} # table_name -> List[Variable]
        self.table_column_to_vars: Dict[Tuple[str, str], List[exp.Column]] = {} # (table_name, column) -> List[Variable]
        self.columnref_to_vars: Dict[Tuple[str, str], List[exp.Column]] = {} # (table_alias, column) -> List[Variable]
        self.uf = UnionFind()
        
        self.tracer = UExprToConstraint()
        DataGenerator.set_random_seed(random_seed)
        self.verbose = verbose
        
        self.query_info = {
            "tables": list(self.scope.expression.find_all(exp.Table)),
            "columns": list(self.scope.expression.find_all(exp.Column)),
            # "where_conditions": self._extract_conditions(parsed),
            "joins": [join_condition(join) for join in self.scope.expression.find_all(exp.Join)],
            "group_by": list(self.scope.expression.find_all(exp.Group)),
            "order_by": list(self.scope.expression.find_all(exp.Order)),
            "limit": self.scope.expression.args.get("limit"),
        }
        
    @property
    def dialect(self) -> Optional[str]:
        return self.instance.dialect if self.instance else None
    
    
    def _flatten_foreign_key_info(self, table_to_vars):
        fk_infos = {}
        for local_tbl in table_to_vars:
            tableref = self.get_tableref(local_tbl)
            fks = self.instance.get_foreign_key(tableref)
            for fk in fks:
                local_col = self.instance._normalize_name(fk.expressions[0].name)
                ref_table = self.instance._normalize_name(fk.args.get("reference").find(exp.Table).name, is_table=True)
                ref_col = self.instance._normalize_name(fk.args.get("reference").this.expressions[0].name)
                fk_infos[(local_tbl, local_col)] = (ref_table, ref_col)
        return fk_infos
    @property
    def foreign_keys(self) -> Dict[str, List[exp.ForeignKey]]:
        fks = {}
        for table_alias in self.table_to_var:
            tableref = self.get_tableref(table_alias)
            fks[table_alias] = self.instance.get_foreign_key(tableref)
        return fks
    
    def get_tableref(self, alias_or_name: str) -> str:
        if alias_or_name not in self.table_alias and alias_or_name in self.instance.tables:
            return alias_or_name
        return self.table_alias[alias_or_name]
    
    def _table_alias(self, table_alias:  Optional[Dict] = None) -> str:
        alias = {}
        if table_alias is None:
            for table in self.scope.expression.find_all(exp.Table):
                alias[table.alias_or_name] = self.instance._normalize_name(table.name)
        else:
            alias.update(**table_alias)
        return alias
        
    
    def declare_variable(self, columnref: exp.Column, reuse = True) -> Tuple[str, str]:
        key =(columnref.table, columnref.name)
        if reuse and key in self.variables:
            return self.variables[key]
        table_ref = self.get_tableref(columnref.table)
        if not reuse:
            suffix = len(self.columnref_to_vars.get(key, []))
            while (columnref.table, columnref.name + f"_{suffix}") in self.columnref_to_vars:
                suffix += 1
            key = (columnref.table, columnref.name + f"_{suffix}")
        domain = self.instance.column_domains.get_or_create_pool(table= table_ref, column= columnref.name, alias = ".".join(key))
        variable = exp.Column(this=key[1], table=key[0])
        variable.type = domain.datatype
        self.variables[key] = variable
        self.var_to_columnref[key] = columnref
        self.table_to_vars.setdefault(table_ref, []).append(variable)
        self.columnref_to_vars.setdefault((columnref.table, columnref.name), []).append(variable)
        self.table_column_to_vars.setdefault((table_ref, columnref.name), []).append(variable)
        self.uf.find(".".join(key))
        if self.verbose:
            logger.info(f"Declared variable: {str(variable)} for column: {columnref}")
        return key
    
    def declare_constraint(
        self, label, constraints: Union[Symbol, List[Symbol]]
    ):
        if not isinstance(constraints, list):
            constraints = [constraints]
        self.constraints.setdefault(label, []).extend(constraints)
    
    def _declare_fk_constraints(self):
        fk_infos = self._flatten_foreign_key_info(self.table_to_vars)
        for local_tbl, local_col in fk_infos:
            ref_table, ref_col = fk_infos[(local_tbl, local_col)]
            existing_values = self.instance.get_column_data(table_name=ref_table, column_name=ref_col)
            concretes = [convert_to_literal(d.args.get('concrete'), d.type) for d in existing_values]
            for variable in self.table_column_to_vars.get((ref_table, ref_col), []):
                concretes.append(variable)
            for variable in self.table_column_to_vars.get((local_tbl, local_col), []):
                fk_constraints = [variable.eq(c) for c in concretes]
                fk_constraint = reduce(lambda x, y: x.or_(y), fk_constraints)
                self.declare_constraint("foreign_key", fk_constraint)
                
    def _declare_pk_constraints(self):
        for (table_name, column_name), variables in self.table_column_to_vars.items():
            pk_columns = self.instance.get_primary_key(table_name)
            if column_name not in pk_columns:
                continue
            existing_values = self.instance.get_column_data(table_name=table_name, column_name=column_name)
            concretes = [ convert_to_literal(d.concrete, d.type) for d in existing_values]
            self.declare_constraint("primary_key", exp.Distinct(expressions= concretes + variables, _type="bool"))
            self.declare_constraint("not_null", [v.is_(exp.Null(_type = v.type)).not_() for v in variables])
        
    def _declare_column_constraints(self):
        
        for (table_name, column_name), variables in self.table_column_to_vars.items():
            for column_constraint in self.instance.get_column_constraints(table_name, column_name):
                existing_values = self.instance.get_column_data(table_name=table_name, column_name=column_name)
                concretes = [ convert_to_literal(d.concrete, d.datatype) for d in existing_values]
                if isinstance(column_constraint.kind, (exp.PrimaryKeyColumnConstraint, exp.UniqueColumnConstraint)):
                    self.declare_constraint("unique_constraint", exp.Distinct(expressions= concretes + variables, _type="bool"))
                    
                if isinstance(column_constraint.kind, (exp.NotNullColumnConstraint, exp.PrimaryKeyColumnConstraint)):
                    if not column_constraint.kind.args.get("allow_null", False):
                        self.declare_constraint("not_null", [v.is_(exp.Null(_type = v.datatype)).not_() for v in variables])
                    
                elif isinstance(column_constraint.kind, exp.CheckColumnConstraint):
                    ...
                
    def _declare_db_constraints(self) -> List:
        self._declare_pk_constraints()
        self._declare_fk_constraints()
        self._declare_column_constraints()
                
    def _declare_variables(self, pattern: Tuple[PBit]):
        path = self.tracer.leaves[pattern].get_path_to_root()
        for bit, node in zip(pattern, path[1:]):
            sql_condition = node.sql_condition
            columnrefs = set(sql_condition.find_all(exp.Column))
            variables = [self.declare_variable(columnref) for columnref in columnrefs]
            self.uf.find(variables[0])
            for i in range(1, len(variables)):
                self.uf.union(variables[0], variables[i])
            
        # Declare foreign key constraints
        fk_infos = self._flatten_foreign_key_info(self.table_to_vars)
        
        q = deque(set(self.table_column_to_vars.keys()))
        while q:
            local_table, local_col = q.popleft()
            if (local_table, local_col) not in fk_infos:
                continue
            ref_table_name, ref_col_name = fk_infos[(local_table, local_col)]
            domain = self.instance.column_domains.get_or_create_pool(table= ref_table_name, column= ref_col_name)
            ref_column = exp.Column(this = ref_col_name, table = ref_table_name, _type = domain.datatype)
            ref_column.type = domain.datatype
            variable = self.declare_variable(ref_column, reuse= False)
            q.append((ref_table_name, ref_col_name))
            self.uf.union((local_table, local_col), variable)
            
    
    def _reset(self):
        self.variables.clear()
        self.constraints.clear()
        self.var_to_columnref.clear()
        self.table_to_vars.clear()
        self.columnref_to_vars.clear()
        self.table_column_to_vars.clear()
        self.uf.clear()
        

    def _prune_constraints(self, pattern: Tuple[PBit], paths: List[Constraint]):
        # Implement constraint pruning logic here
        """
            Prune constraints that are not relevant to the given pattern.
            1. LEFT JOIN: Remove constraints related to the right table if the join condition is not satisfied.
            2. RIGHT JOIN: Remove constraints related to the left table if the join condition is not satisfied.
            3. HAVING: Remove constraints that are not relevant to the HAVING clause.
        """
        for join in self.query_info["joins"]:
            if join['side'].upper() == "LEFT" and PBit.JOIN_LEFT in pattern:
                joined_table = join['join_key'][0].table
                removed_constraints = []
                for constraint in self.constraints.get(PBit.TRUE,  []):
                    for column in constraint.find_all(exp.Column):
                        if column.table == joined_table:
                            # Remove constraint
                            removed_constraints.append(constraint)
                for rc in removed_constraints:
                    self.constraints[PBit.TRUE].remove(rc)
        
    def _declare_coverage_constraints(self, plausible, skips : Optional[Set] = None):
        skips = skips or set()
        from src.parseval.uexpr.checks import Declare
        declare = Declare(self)
        path = list(reversed(plausible.get_path_to_root()[1:]))
        patterns = list(reversed(plausible.pattern()))
        for bit, node in zip(patterns, path[1:]):            
            if node.step_type in skips:
                continue
            if not declare.declare(bit, node, {}):
                return
    
    def _print(self, pattern: Tuple[PBit]):
        if not self.verbose:
            return
        lines = [f"=====================Coverage Constraints For {'/'.join(str(p.value) for p in pattern)}====================================="]
        for label, cons in self.constraints.items():
            for c in cons:
                lines.append(str(c))
        logger.info("\n".join(lines))
        
    def _generate(self, pattern: Tuple[PBit], plausible: PlausibleBranch):
        if plausible.plausible_type == PlausibleType.INFEASIBLE:
            return
        
        concretes = {}
        
        self._declare_variables(pattern)
        self._declare_db_constraints()
        self._declare_coverage_constraints(plausible)
        
        self._print(pattern)
        
        solver = SMTSolver(self.variables, verbose= self.verbose)
        
        for label, cons in self.constraints.items():
            for c in cons:
                solver.add(solver._to_z3_expr(c))
        
        result = solver.solve()
        
        if result is None:
            plausible.mark_infeasible()
            if self.verbose:
                logger.debug(f"Pattern {pattern} is infeasible.")
            return {}
            
        for key in self.variables:
            var_name = ".".join(key)
            if var_name in result:
                value = result[var_name]
                columnref = self.var_to_columnref[key]
                tbl_name = self.get_tableref(columnref.table)
                concretes.setdefault(tbl_name, {}).setdefault(columnref.name, []).append(value)
        return concretes
        
        
    def generate(self, timeout = 360):
        from .configuration import Config
        config = Config()
        skips = set()
        encoder = PlanEncoder(scope= self.scope, instance= self.instance, trace = self.tracer, dialect= self.dialect)
        print("Encoding plan...")
        ctx = encoder.encode()
        q = deque([self.tracer.next_path(config= config, skips= skips)])
        print(f"Start generating data..., {len(q)}, ")
        while q:
            pattern, plausible = q.popleft()
            print(f"processing pattern: {pattern}")
            if pattern is None:
                break
            concretes = self._generate(pattern, plausible)
            if concretes:
                logger.info(f"Generated concretes for pattern {pattern}: {concretes}")
                self.instance.create_rows(concretes)
            self._reset()
            encoder.encode()
            q.append(self.tracer.next_path(config= config, skips= skips))
        from src.parseval.to_dot import display_uexpr
        display_uexpr(self.tracer.root).write(
            "examples/tests/dot_coverage_" + self.instance.name + ".png", format="png"
        )
        


def _table_alias(instance: Instance, expr: exp.Expression) -> str:
    alias = {}
    for table in expr.find_all(exp.Table):
        alias[table.alias_or_name] = instance._normalize_name(table.name)
    return alias

def get_parent(e):
    if e.parent is None:
        return None
    if isinstance(e.parent, (exp.Paren, exp.Subquery)):
        return get_parent(e.parent)
    return e.parent

def dbgenerate(ddls, query, workspace, dialect: str = "sqlite", random_seed: int = 42) -> List[Dict]:
    """
    Generate data based on DDLs and a query.

    Args:
        ddls: List of DDL statements.
        query: The SQL query.
        dialect: SQL dialect to use.
    """
    from sqlglot.optimizer.scope import Scope, traverse_scope, walk_in_scope, find_all_in_scope, build_scope
    from parseval.query import preprocess_sql
    context = {}
    visited = set()
    
    instance = Instance(ddls=ddls, name="test", dialect=dialect)
    expr = preprocess_sql(query, instance, dialect= dialect)
    table_alias = _table_alias(instance, expr)
    visited = set()
    root = build_scope(expression= expr)
    queue = deque([root])
    index = 0
    logger.info(f"Starting data generation for query: {query}")
    while queue:
        scope = queue.popleft()
        proceed = True
        # print(f'Processing scope with expression: {scope.expression.sql(dialect=dialect)}')
        # logger.info(f"Processing scope with expression: {scope.expression.sql(dialect=dialect)}")
        # correlated_scopes = []
        for sub_scope in scope.subquery_scopes:
            # if sub_scope.is_correlated_subquery:
            #     correlated_scopes.append(sub_scope)
            if sub_scope.is_subquery and not sub_scope.is_correlated_subquery:
                if sub_scope not in visited:
                    queue.append(sub_scope)
                    proceed = False
                    break
                else:
                    parent = get_parent(sub_scope.expression)
                    dtype = None
                    if isinstance(parent, exp.Predicate):
                        for r in [parent.left, parent.right]:
                            if r is not sub_scope.expression.parent:
                                dtype = r.type
                        is_string = False
                        if dtype is not None:
                            dtype = exp.DataType.build(dtype)
                            is_string = dtype.is_type(*exp.DataType.TEXT_TYPES)
                        
                        concrete = context[sub_scope][0][0]
                        print(context[sub_scope])
                        print(f'concrete is {concrete}, dtype is {dtype}, is_string: {is_string}')
                        if is_string:
                            new = exp.Literal.string(concrete)
                        else:
                            new = exp.Literal.number(concrete)
                        new.type = dtype
                        # new = exp.Literal(this = concrete, _type = dtype, is_string = is_string)
                        scope.replace(sub_scope.expression.parent, new)
        if not proceed:
            queue.append(scope)
            continue
        print(f"Generating data for scope with expression: {scope.expression.sql(dialect=dialect)}")
        tmp_db_name = f"{instance.name}_{index}"
        instance.name = tmp_db_name
        logger.info(f"Generating data for scope with expression: {scope.expression.sql(dialect=dialect)}")
        print(f'scope: {type(scope)}')
        generator = DataGenerator(scope=scope, instance= instance, table_alias= table_alias, name = tmp_db_name, workspace= workspace, random_seed=random_seed, verbose= True)
        generator.generate()
        
        if scope.is_subquery:
            
            instance.to_db(workspace)
            from parseval.db_manager import DBManager
            with DBManager().get_connection(host_or_path= workspace, database= tmp_db_name + ".sqlite", dialect= dialect) as conn:
                results = conn.execute(scope.expression.sql(dialect=dialect), fetch='all')
                context[scope] = results
            index += 1
        
        visited.add(scope)
        
            
    
    
    # q = deque(list(traverse_scope(expr)))
    # while q:
    #     scope = q.popleft()
    #     scope.ref_count
        
    #     if any([s not in visited for s in scope.selected_sources]):
    #         continue
        
    #     if scope.external_columns:
    #         ...
        
    #     if scope.is_correlated_subquery:
    #         ...
        
    #     scope.is_subquery
        
    #     ...
    