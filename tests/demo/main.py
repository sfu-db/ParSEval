"""
SQL非空查询数据生成框架
架构层次：
1. 解析层：解析Schema和Query
2. 分析层：提取约束和依赖关系
3. 规划层：制定数据生成策略
4. 生成层：执行数据生成
5. 验证层：验证查询结果
"""

import sqlglot
import sqlglot.expressions as exp
from typing import Dict, List, Set, Tuple, Optional, Any
from dataclasses import dataclass, field
from collections import defaultdict
import random
from enum import Enum
import logging

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class DataType(Enum):
    """支持的数据类型"""
    INTEGER = "INTEGER"
    STRING = "STRING"
    DECIMAL = "DECIMAL"
    DATE = "DATE"
    BOOLEAN = "BOOLEAN"
    TIMESTAMP = "TIMESTAMP"

@dataclass
class Column:
    """列定义"""
    name: str
    data_type: DataType
    nullable: bool = True
    is_primary_key: bool = False
    is_foreign_key: bool = False
    references: Optional[Tuple[str, str]] = None  # (table_name, column_name)
    unique: bool = False
    default_value: Optional[Any] = None
    
@dataclass
class Table:
    """表定义"""
    name: str
    columns: Dict[str, Column]
    primary_keys: List[str] = field(default_factory=list)
    foreign_keys: List[Tuple[str, str, str, str]] = field(default_factory=list)  # (col, ref_table, ref_col)
    
@dataclass
class Constraint:
    """约束条件"""
    column: str
    operator: str  # =, >, <, >=, <=, !=, LIKE, IN, BETWEEN
    value: Any
    table: str = ""
    
@dataclass
class JoinCondition:
    """连接条件"""
    left_table: str
    left_column: str
    right_table: str
    right_column: str
    join_type: str  # INNER, LEFT, RIGHT, FULL
    
class SQLDataGenerator:
    """SQL数据生成器主类"""
    
    def __init__(self, schema_sql: str, query_sql: str):
        """
        初始化数据生成器
        
        Args:
            schema_sql: 数据库Schema的SQL语句
            query_sql: 目标查询的SQL语句
        """
        self.schema_sql = schema_sql
        self.query_sql = query_sql
        self.schema = self.parse_schema()
        self.query_ast = self.parse_query()
        self.optimized_ast = self.optimize_query()
        self.constraints = self.extract_constraints()
        self.join_conditions = self.extract_join_conditions()
        self.aggregation_info = self.extract_aggregation_info()
        
    def parse_schema(self) -> Dict[str, Table]:
        """解析数据库Schema"""
        schema = {}
        
        # 使用SQLGlot解析CREATE TABLE语句
        for statement in sqlglot.parse(self.schema_sql):
            if isinstance(statement, exp.Create):
                table_name = statement.this.name
                columns = {}
                primary_keys = []
                foreign_keys = []
                
                for column_def in statement.expressions:
                    if isinstance(column_def, exp.ColumnDef):
                        col_name = column_def.this.name
                        
                        # 解析数据类型
                        data_type_str = str(column_def.kind).split('(')[0].upper()
                        try:
                            data_type = DataType(data_type_str)
                        except ValueError:
                            data_type = DataType.STRING
                        
                        # 解析约束
                        nullable = True
                        is_primary = False
                        is_foreign = False
                        unique = False
                        default_val = None
                        references = None
                        
                        for constraint in column_def.constraints:
                            if isinstance(constraint, exp.NotNullColumnConstraint):
                                nullable = False
                            elif isinstance(constraint, exp.PrimaryKeyColumnConstraint):
                                is_primary = True
                                primary_keys.append(col_name)
                            elif isinstance(constraint, exp.UniqueColumnConstraint):
                                unique = True
                            elif isinstance(constraint, exp.DefaultColumnConstraint):
                                default_val = constraint.this.name
                        
                        column = Column(
                            name=col_name,
                            data_type=data_type,
                            nullable=nullable,
                            is_primary_key=is_primary,
                            is_foreign_key=is_foreign,
                            references=references,
                            unique=unique,
                            default_value=default_val
                        )
                        columns[col_name] = column
                
                table = Table(
                    name=table_name,
                    columns=columns,
                    primary_keys=primary_keys,
                    foreign_keys=foreign_keys
                )
                schema[table_name] = table
        
        return schema
    
    def parse_query(self) -> exp.Expression:
        """解析查询SQL"""
        return sqlglot.parse_one(self.query_sql)
    
    def optimize_query(self) -> exp.Expression:
        """优化查询语句"""
        try:
            # 应用SQLGlot的优化器
            optimized = self.query_ast
            
            from sqlglot.optimizer.qualify import qualify
            from sqlglot.optimizer.normalize import normalize
            from sqlglot.optimizer.eliminate_joins import eliminate_joins
            
            # 移除不必要的列
            optimized = qualify(optimized)
            optimized = normalize(optimized)
            optimized = eliminate_joins(optimized)
            
            logger.info("查询优化完成")
            return optimized
        except Exception as e:
            logger.warning(f"查询优化失败: {e}")
            return self.query_ast
    
    def extract_constraints(self) -> Dict[str, List[Constraint]]:
        """从WHERE子句提取约束条件"""
        constraints = defaultdict(list)
        
        def extract_from_expression(expr, table_name=""):
            """递归提取约束"""
            if isinstance(expr, exp.Where):
                extract_from_expression(expr.this, table_name)
            elif isinstance(expr, exp.And) or isinstance(expr, exp.Or):
                extract_from_expression(expr.left, table_name)
                extract_from_expression(expr.right, table_name)
            elif isinstance(expr, exp.EQ) or isinstance(expr, exp.GT) or \
                 isinstance(expr, exp.LT) or isinstance(expr, exp.GTE) or \
                 isinstance(expr, exp.LTE) or isinstance(expr, exp.NEQ):
                
                left = expr.left
                right = expr.right
                
                # 处理列在左边的情况
                if isinstance(left, exp.Column):
                    col_name = left.name
                    table = left.table or table_name
                    operator = type(expr).__name__.upper()
                    
                    # 提取值
                    if isinstance(right, (exp.Literal, exp.Boolean, exp.Null)):
                        value = right.this
                    elif isinstance(right, exp.Paren):
                        value = extract_from_expression(right.this)
                    else:
                        value = str(right)
                    
                    if table:
                        constraints[table].append(
                            Constraint(column=col_name, operator=operator, value=value, table=table)
                        )
                
                # 处理列在右边的情况
                elif isinstance(right, exp.Column):
                    col_name = right.name
                    table = right.table or table_name
                    operator = type(expr).__name__.upper()
                    
                    # 反转操作符
                    operator_map = {
                        'EQ': 'EQ', 'NEQ': 'NEQ',
                        'GT': 'LT', 'LT': 'GT',
                        'GTE': 'LTE', 'LTE': 'GTE'
                    }
                    operator = operator_map.get(operator, operator)
                    
                    if isinstance(left, (exp.Literal, exp.Boolean, exp.Null)):
                        value = left.this
                    else:
                        value = str(left)
                    
                    if table:
                        constraints[table].append(
                            Constraint(column=col_name, operator=operator, value=value, table=table)
                        )
        
        # 从优化后的AST提取
        for table in self.optimized_ast.find_all(exp.Table):
            table_name = table.name
            # 查找相关的WHERE条件
            where_clause = self.optimized_ast.find(exp.Where)
            if where_clause:
                extract_from_expression(where_clause.this, table_name)
        
        return dict(constraints)
    
    def extract_join_conditions(self) -> List[JoinCondition]:
        """提取JOIN条件"""
        joins = []
        
        for join in self.optimized_ast.find_all(exp.Join):
            join_type = "INNER"
            if join.args.get("kind"):
                join_type = join.args["kind"].upper()
            
            on_clause = join.args.get("on")
            if on_clause:
                if isinstance(on_clause, exp.EQ):
                    left = on_clause.left
                    right = on_clause.right
                    
                    if isinstance(left, exp.Column) and isinstance(right, exp.Column):
                        joins.append(JoinCondition(
                            left_table=left.table or "",
                            left_column=left.name,
                            right_table=right.table or "",
                            right_column=right.name,
                            join_type=join_type
                        ))
        
        return joins
    
    def extract_aggregation_info(self) -> Dict[str, Any]:
        """提取聚合函数信息"""
        aggregations = {
            'has_aggregation': False,
            'aggregate_columns': [],
            'group_by_columns': []
        }
        
        # 检查聚合函数
        for func in self.optimized_ast.find_all(exp.AggFunc):
            aggregations['has_aggregation'] = True
            aggregations['aggregate_columns'].append({
                'function': func.__class__.__name__,
                'column': str(func.this) if func.this else "*"
            })
        
        # 检查GROUP BY
        group_by = self.optimized_ast.find(exp.Group)
        if group_by:
            for expr in group_by.expressions:
                if isinstance(expr, exp.Column):
                    aggregations['group_by_columns'].append({
                        'table': expr.table or "",
                        'column': expr.name
                    })
        
        return aggregations
    
    def generate_data_strategy(self) -> Dict[str, Dict[str, Any]]:
        """制定数据生成策略"""
        strategy = {}
        
        # 确定需要生成数据的表
        involved_tables = set()
        for table in self.optimized_ast.find_all(exp.Table):
            involved_tables.add(table.name)
        
        # 为每个表制定策略
        for table_name in involved_tables:
            if table_name in self.schema:
                table_strategy = {
                    'min_rows': 1,
                    'max_rows': 100,
                    'column_strategies': {},
                    'dependencies': [],
                    'must_have_rows': True  # 是否必须有数据
                }
                
                table = self.schema[table_name]
                
                # 处理约束条件
                table_constraints = self.constraints.get(table_name, [])
                for col_name, column in table.columns.items():
                    col_strategy = {
                        'data_type': column.data_type,
                        'nullable': column.nullable,
                        'unique': column.unique,
                        'constraints': [],
                        'value_range': self._get_default_range(column.data_type)
                    }
                    
                    # 应用约束
                    for constraint in table_constraints:
                        if constraint.column == col_name:
                            col_strategy['constraints'].append({
                                'operator': constraint.operator,
                                'value': constraint.value
                            })
                            
                            # 根据约束调整值范围
                            col_strategy['value_range'] = self._adjust_range_by_constraint(
                                col_strategy['value_range'],
                                constraint.operator,
                                constraint.value,
                                column.data_type
                            )
                    
                    # 处理外键约束
                    if column.is_foreign_key and column.references:
                        ref_table, ref_col = column.references
                        table_strategy['dependencies'].append(ref_table)
                        col_strategy['references'] = {
                            'table': ref_table,
                            'column': ref_col
                        }
                    
                    table_strategy['column_strategies'][col_name] = col_strategy
                
                # 处理JOIN条件
                for join in self.join_conditions:
                    if join.left_table == table_name or join.right_table == table_name:
                        other_table = join.right_table if join.left_table == table_name else join.left_table
                        if other_table not in table_strategy['dependencies']:
                            table_strategy['dependencies'].append(other_table)
                
                strategy[table_name] = table_strategy
        
        return strategy
    
    def _get_default_range(self, data_type: DataType) -> Dict[str, Any]:
        """获取默认值范围"""
        ranges = {
            DataType.INTEGER: {'min': 1, 'max': 1000},
            DataType.STRING: {'min_length': 1, 'max_length': 50, 'pattern': 'string_'},
            DataType.DECIMAL: {'min': 0.0, 'max': 1000.0, 'precision': 2},
            DataType.DATE: {'start': '2023-01-01', 'end': '2023-12-31'},
            DataType.BOOLEAN: {'values': [True, False]},
            DataType.TIMESTAMP: {'start': '2023-01-01 00:00:00', 'end': '2023-12-31 23:59:59'}
        }
        return ranges.get(data_type, {})
    
    def _adjust_range_by_constraint(self, value_range: Dict[str, Any], 
                                   operator: str, value: Any, 
                                   data_type: DataType) -> Dict[str, Any]:
        """根据约束条件调整值范围"""
        if data_type == DataType.INTEGER or data_type == DataType.DECIMAL:
            value = float(value) if data_type == DataType.DECIMAL else int(value)
            
            if operator == 'GT':
                value_range['min'] = max(value_range.get('min', -float('inf')), value + 1)
            elif operator == 'GTE':
                value_range['min'] = max(value_range.get('min', -float('inf')), value)
            elif operator == 'LT':
                value_range['max'] = min(value_range.get('max', float('inf')), value - 1)
            elif operator == 'LTE':
                value_range['max'] = min(value_range.get('max', float('inf')), value)
            elif operator == 'EQ':
                value_range['min'] = value
                value_range['max'] = value
        
        return value_range
    
    def generate_data(self) -> Dict[str, List[Dict[str, Any]]]:
        """生成数据"""
        strategy = self.generate_data_strategy()
        generated_data = {}
        
        # 按依赖顺序排序表
        table_order = self._topological_sort(strategy)
        
        # 按顺序生成数据
        for table_name in table_order:
            if table_name in strategy:
                table_strategy = strategy[table_name]
                table_data = []
                
                # 确定行数（考虑GROUP BY和聚合）
                if self.aggregation_info['has_aggregation']:
                    group_by_count = len(self.aggregation_info['group_by_columns']) or 1
                    num_rows = max(1, random.randint(group_by_count, group_by_count * 3))
                else:
                    num_rows = random.randint(table_strategy['min_rows'], table_strategy['max_rows'])
                
                # 生成每一行
                for row_idx in range(num_rows):
                    row = {}
                    col_strategies = table_strategy['column_strategies']
                    
                    for col_name, col_strategy in col_strategies.items():
                        # 处理外键引用
                        if 'references' in col_strategy:
                            ref_table = col_strategy['references']['table']
                            ref_col = col_strategy['references']['column']
                            
                            if ref_table in generated_data and generated_data[ref_table]:
                                ref_value = random.choice(generated_data[ref_table])[ref_col]
                                row[col_name] = ref_value
                                continue
                        
                        # 生成值
                        value = self._generate_column_value(col_strategy, row_idx)
                        
                        # 确保满足约束
                        for constraint in col_strategy['constraints']:
                            value = self._ensure_constraint(
                                value, 
                                constraint['operator'], 
                                constraint['value'],
                                col_strategy['data_type']
                            )
                        
                        row[col_name] = value
                    
                    table_data.append(row)
                
                # 确保JOIN条件能被满足（至少一行能参与JOIN）
                if self.join_conditions and table_strategy['must_have_rows']:
                    self._ensure_join_conditions(table_name, table_data, generated_data)
                
                generated_data[table_name] = table_data
        
        return generated_data
    
    def _topological_sort(self, strategy: Dict[str, Dict[str, Any]]) -> List[str]:
        """拓扑排序表（基于依赖关系）"""
        visited = set()
        stack = []
        
        def dfs(table_name):
            if table_name not in visited:
                visited.add(table_name)
                if table_name in strategy:
                    for dep in strategy[table_name]['dependencies']:
                        dfs(dep)
                stack.append(table_name)
        
        for table_name in strategy.keys():
            if table_name not in visited:
                dfs(table_name)
        
        return stack
    
    def _generate_column_value(self, col_strategy: Dict[str, Any], row_idx: int) -> Any:
        """生成列值"""
        data_type = col_strategy['data_type']
        value_range = col_strategy.get('value_range', {})
        
        if data_type == DataType.INTEGER:
            min_val = value_range.get('min', 1)
            max_val = value_range.get('max', 1000)
            return random.randint(min_val, max_val)
        
        elif data_type == DataType.STRING:
            prefix = value_range.get('pattern', 'val_')
            suffix = str(row_idx)
            return f"{prefix}{suffix}"
        
        elif data_type == DataType.DECIMAL:
            min_val = value_range.get('min', 0.0)
            max_val = value_range.get('max', 1000.0)
            precision = value_range.get('precision', 2)
            return round(random.uniform(min_val, max_val), precision)
        
        elif data_type == DataType.BOOLEAN:
            values = value_range.get('values', [True, False])
            return random.choice(values)
        
        elif data_type == DataType.DATE:
            # 简化为字符串
            return f"2023-{random.randint(1, 12):02d}-{random.randint(1, 28):02d}"
        
        elif data_type == DataType.TIMESTAMP:
            return f"2023-{random.randint(1, 12):02d}-{random.randint(1, 28):02d} " \
                   f"{random.randint(0, 23):02d}:{random.randint(0, 59):02d}:{random.randint(0, 59):02d}"
        
        return None
    
    def _ensure_constraint(self, value: Any, operator: str, target: Any, data_type: DataType) -> Any:
        """确保值满足约束"""
        if operator == 'EQ':
            return target if target is not None else value
        elif operator == 'NEQ':
            return value if value != target else value + 1
        return value
    
    def _ensure_join_conditions(self, table_name: str, table_data: List[Dict[str, Any]], 
                               all_data: Dict[str, List[Dict[str, Any]]]):
        """确保至少一行数据满足JOIN条件"""
        for join in self.join_conditions:
            if join.left_table == table_name or join.right_table == table_name:
                other_table = join.right_table if join.left_table == table_name else join.left_table
                
                if other_table in all_data and all_data[other_table]:
                    # 从另一个表选取一个值
                    other_value = random.choice(all_data[other_table])[
                        join.right_column if join.left_table == table_name else join.left_column
                    ]
                    
                    # 确保至少一行有这个值
                    if not any(row.get(join.left_column if join.left_table == table_name else join.right_column) == other_value 
                              for row in table_data):
                        # 修改第一行的对应列
                        if table_data:
                            if join.left_table == table_name:
                                table_data[0][join.left_column] = other_value
                            else:
                                table_data[0][join.right_column] = other_value
    
    def validate_query(self, data: Dict[str, List[Dict[str, Any]]], 
                       connection_string: Optional[str] = None) -> Tuple[bool, Any]:
        """
        验证查询是否返回非空结果
        
        Args:
            data: 生成的数据
            connection_string: 数据库连接字符串（可选）
            
        Returns:
            Tuple[bool, result]: 是否非空，查询结果
        """
        if connection_string:
            # 如果提供了数据库连接，可以执行实际查询
            return self._execute_real_query(data, connection_string)
        else:
            # 简化的内存验证
            return self._simulate_query(data)
    
    def _simulate_query(self, data: Dict[str, List[Dict[str, Any]]]) -> Tuple[bool, Any]:
        """模拟查询执行（简化版本）"""
        # 检查是否有数据
        involved_tables = set()
        for table in self.optimized_ast.find_all(exp.Table):
            involved_tables.add(table.name)
        
        for table_name in involved_tables:
            if table_name not in data or not data[table_name]:
                return False, f"Table {table_name} has no data"
        
        # 检查JOIN条件是否可满足
        for join in self.join_conditions:
            left_data = data.get(join.left_table, [])
            right_data = data.get(join.right_table, [])
            
            if not left_data or not right_data:
                return False, f"Missing data for join between {join.left_table} and {join.right_table}"
            
            # 检查是否有匹配的值
            left_values = {row[join.left_column] for row in left_data if join.left_column in row}
            right_values = {row[join.right_column] for row in right_data if join.right_column in row}
            
            if not left_values.intersection(right_values) and join.join_type == 'INNER':
                return False, f"No matching values for join condition"
        
        return True, "Query would return non-empty result"
    
    def generate_sql_inserts(self, data: Dict[str, List[Dict[str, Any]]]) -> List[str]:
        """生成SQL INSERT语句"""
        insert_statements = []
        
        for table_name, rows in data.items():
            if rows:
                columns = list(rows[0].keys())
                for row in rows:
                    values = []
                    for col in columns:
                        value = row[col]
                        if isinstance(value, str):
                            values.append(f"'{value}'")
                        elif value is None:
                            values.append("NULL")
                        else:
                            values.append(str(value))
                    
                    insert_sql = f"INSERT INTO {table_name} ({', '.join(columns)}) VALUES ({', '.join(values)});"
                    insert_statements.append(insert_sql)
        
        return insert_statements
    
class AdvancedSQLDataGenerator(SQLDataGenerator):
    """高级数据生成器，支持更多特性"""
    
    def __init__(self, schema_sql: str, query_sql: str):
        super().__init__(schema_sql, query_sql)
        self.subqueries = self.extract_subqueries()
        self.window_functions = self.extract_window_functions()
        
    def extract_subqueries(self) -> List[exp.Expression]:
        """提取子查询"""
        subqueries = []
        
        def find_subqueries(expr):
            if isinstance(expr, exp.Subquery):
                subqueries.append(expr.this)
            for child in expr.children:
                if isinstance(child, exp.Expression):
                    find_subqueries(child)
        
        find_subqueries(self.optimized_ast)
        return subqueries
    
    def extract_window_functions(self) -> List[Dict[str, Any]]:
        """提取窗口函数"""
        window_funcs = []
        
        for func in self.optimized_ast.find_all(exp.Window):
            window_info = {
                'function': str(func.this),
                'partition_by': [],
                'order_by': [],
                'frame': {}
            }
            
            # 提取PARTITION BY
            if func.args.get('partition_by'):
                for expr in func.args['partition_by'].expressions:
                    if isinstance(expr, exp.Column):
                        window_info['partition_by'].append({
                            'table': expr.table or '',
                            'column': expr.name
                        })
            
            window_funcs.append(window_info)
        
        return window_funcs
    
    def generate_data_with_patterns(self, patterns: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
        """基于模式生成数据"""
        base_data = self.generate_data()
        
        # 应用用户定义的模式
        for table_name, table_pattern in patterns.items():
            if table_name in base_data and table_name in self.schema:
                for row in base_data[table_name]:
                    for col_name, pattern in table_pattern.items():
                        if col_name in self.schema[table_name].columns:
                            if callable(pattern):
                                row[col_name] = pattern(row)
                            else:
                                row[col_name] = pattern
        
        return base_data


class DataGenerationCLI:
    """命令行界面"""
    
    def __init__(self):
        self.generator = None
        
    def run_interactive(self):
        """交互式运行"""
        print("=== SQL数据生成框架 ===")
        
        # 输入Schema
        print("\n请输入数据库Schema（以空行结束）:")
        schema_lines = []
        while True:
            line = input()
            if line.strip() == "":
                break
            schema_lines.append(line)
        schema_sql = "\n".join(schema_lines)
        
        # 输入查询
        print("\n请输入SQL查询:")
        query_sql = input()
        
        # 创建生成器
        try:
            self.generator = SQLDataGenerator(schema_sql, query_sql)
            print("\nSchema和查询解析成功！")
            
            # 生成数据
            print("\n正在生成数据...")
            data = self.generator.generate_data()
            
            # 显示统计信息
            self.show_statistics(data)
            
            # 询问是否保存
            save = input("\n是否保存为SQL文件？(y/n): ").lower()
            if save == 'y':
                self.save_to_sql(data)
                
        except Exception as e:
            print(f"错误: {e}")
    
    def show_statistics(self, data: Dict[str, List[Dict[str, Any]]]):
        """显示统计信息"""
        print("\n=== 生成数据统计 ===")
        total_rows = 0
        for table_name, rows in data.items():
            print(f"表 {table_name}: {len(rows)} 行")
            total_rows += len(rows)
        print(f"总计: {total_rows} 行")
    
    def save_to_sql(self, data: Dict[str, List[Dict[str, Any]]], filename: str = "generated_data.sql"):
        """保存为SQL文件"""
        inserts = self.generator.generate_sql_inserts(data)
        with open(filename, 'w') as f:
            f.write("-- 自动生成的数据\n")
            f.write("-- 用于确保查询非空\n\n")
            for insert in inserts:
                f.write(insert + "\n")
        print(f"数据已保存到 {filename}")


# 工厂类
class DataGeneratorFactory:
    """数据生成器工厂"""
    
    @staticmethod
    def create_generator(generator_type: str, schema_sql: str, query_sql: str) -> SQLDataGenerator:
        """创建生成器实例"""
        if generator_type == "basic":
            return SQLDataGenerator(schema_sql, query_sql)
        elif generator_type == "advanced":
            return AdvancedSQLDataGenerator(schema_sql, query_sql)
        else:
            raise ValueError(f"未知的生成器类型: {generator_type}")
        
# 示例使用
def main():
    # 示例Schema
    schema_sql = """
    CREATE TABLE users (
        id INTEGER PRIMARY KEY,
        name VARCHAR(100) NOT NULL,
        age INTEGER,
        email VARCHAR(255) UNIQUE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    
    CREATE TABLE orders (
        order_id INTEGER PRIMARY KEY,
        user_id INTEGER REFERENCES users(id),
        amount DECIMAL(10, 2),
        status VARCHAR(50),
        order_date DATE
    );
    
    CREATE TABLE products (
        product_id INTEGER PRIMARY KEY,
        name VARCHAR(200) NOT NULL,
        price DECIMAL(10, 2),
        category VARCHAR(100)
    );
    """
    
    # 示例查询
    query_sql = """
    SELECT u.name, u.email, SUM(o.amount) as total_spent
    FROM users u
    JOIN orders o ON u.id = o.user_id
    WHERE u.age > 18 AND o.status = 'completed'
    GROUP BY u.id, u.name, u.email
    HAVING SUM(o.amount) > 1000
    ORDER BY total_spent DESC;
    """
    
    # 创建生成器
    generator = SQLDataGenerator(schema_sql, query_sql)
    
    # 生成数据
    generated_data = generator.generate_data()
    
    print("=== 生成的数据预览 ===")
    print(generated_data)
    
    # 输出生成的INSERT语句
    inserts = generator.generate_sql_inserts(generated_data)
    for insert in inserts[:5]:  # 只显示前5条
        print(insert)
    
    # 验证查询
    is_valid, result = generator.validate_query(generated_data)
    print(f"\n查询验证结果: {is_valid}")
    print(f"验证详情: {result}")
    
    # 查看分析信息
    print(f"\n聚合信息: {generator.aggregation_info}")
    print(f"约束条件: {generator.constraints}")
    print(f"JOIN条件: {generator.join_conditions}")


if __name__ == "__main__":
    main()