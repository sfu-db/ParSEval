from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Optional, List, Dict, Any, TYPE_CHECKING
from dataclasses import dataclass, field
from parseval.db_manager import DBManager
from parseval.helper import compare_df
from parseval.configuration import Config
from parseval.data_generator import DataGenerator
from parseval.query import preprocess_sql
import logging

logger = logging.getLogger("parseval.coverage")


from parseval.instance import Instance

@dataclass
class PreCheckResult:
    state: str
    messages: Dict[str, Any] = field(default_factory=dict)
    
    def __post_init__(self):
        self.state = self.state.upper()
    
class PreCheckingRule(ABC):
    def __init__(self, next_rule: Optional[PreCheckingRule] = None):
        self._next_rule = next_rule
        
    def set_next(self, next_rule: PreCheckingRule) -> PreCheckingRule:
        self._next_rule = next_rule
        return next_rule
    def check(self, **kwargs) -> PreCheckResult:
        """
        Execute current rule and optionally forward to the next rule.

        Stops if the result state is final (EQ, NEQ, SYNTAX_ERROR).
        """
        result = self._check(**kwargs)
        if result.state in {"EQ", "NEQ", "SYNTAX_ERROR", "NON_EMPTY", "EMPTY"}:
            return result
        if self._next_rule is not None:
            return self._next_rule.check(**kwargs)
        return result

    @abstractmethod
    def _check(self, **kwargs) -> PreCheckResult:
        """Internal logic implemented by subclasses."""
        pass
    
    
class AstChecking(PreCheckingRule):
    
    def __init__(self, gold, pred: Optional[str] = None, dialect: Optional[str] = "sqlite", next_rule = None):
        super().__init__(next_rule)
        self.gold = gold
        self.pred = pred
        self.dialect = dialect
        
    def _check(self, **kwargs) -> PreCheckResult:
        if self.pred is None:
            return PreCheckResult(state="UNKNOWN")
        if self.gold.strip() == self.pred.strip():
            return PreCheckResult(state="EQ")
        return PreCheckResult(state="UNKNOWN")
        

class SyntaxChecking(PreCheckingRule):
    
    def __init__(self, schema: str, gold: str, host_or_path: str, database: str, 
                 port: Optional[int] = None, username: Optional[str] = None,  password: Optional[str] = None, pred: Optional[str] = None,
                 dialect: str = "sqlite", next_rule=None):
        super().__init__(next_rule)
        self.schema = schema
        self.host_or_path = host_or_path
        self.database = database
        self.port = port
        self.username = username
        self.password = password
        self.dialect = dialect
        self.gold = gold
        self.pred = pred
        self.dialect = dialect
        
    
    def _check(self, **kwargs):
        
        
        # host_or_path = self.instance.host_or_path
        # dbname = self.instance.name_seq()
        # username = self.instance.username
        # port = self.instance.port
        # password = self.password = self.instance.password
        
        # self.instance.to_db(host_or_path= host_or_path, database= dbname, port= port, username= username, password= password)
        
        
        
        with DBManager().get_connection(host_or_path= self.host_or_path, database= self.database, dialect= self.dialect) as conn:
            
            scm = self.schema.split(";")
            for s in scm:
                print(s)
            # conn.create_tables(self.schema.split(";"))
            
            conn.create_schema(list(self.schema.split(";")))
            try:
                conn.execute(self.gold)
            except Exception as e:
                return PreCheckResult(state= "syntax_error", messages={"gold_error": str(e)})
            if self.pred is not None:
                try:
                    conn.execute(self.pred)
                except Exception as e:
                    return PreCheckResult(state= "syntax_error", messages={"pred_error": str(e)})
        return PreCheckResult(state= "unknown")


class ResultChecking(PreCheckingRule):
    def __init__(self, instance: Instance, gold: str, pred : Optional[str] = None, dialect = "sqlite", config = None, next_rule = None):
        super().__init__(next_rule)
        self.instance = instance
        self.gold = gold
        self.pred = pred
        self.dialect = dialect
        self.config = config
        
    def _check(self, **kwargs):
        if self(self.instance):
            return PreCheckResult(state= "NEQ")
        else:
            return PreCheckResult(state='unknown')
    
    def __call__(self, instance: Instance):
        dbname = instance.name_seq()
        print(f'checking in {dbname}')
        try:
            dbname = instance.to_db(instance.host_or_path, dbname, port= instance.port, username= instance.username, password= instance.password)
            
            print(f'to database from instance properly')
        except Exception as e:
            logger.error(f'Error when generating concrete database: {e}')
            raise e
            return True
        
        with DBManager().get_connection(instance.host_or_path, dbname, instance.username, instance.password, instance.port, instance.dialect) as conn:
            
            gold_ret = conn.execute(self.gold, fetch= "all")
            if self.pred is not None:
                pred_ret = conn.execute(self.pred, fetch= "all")
                if not compare_df(gold_ret, pred_ret, order_matters= False):
                    return True
                return False
            return True if len(gold_ret) > 3 else False

class ExecutionChecking(PreCheckingRule):
    def __init__(self, schema: str, gold: str, host_or_path: str, database: str,
                 port: int, username: str, password: str, pred: Optional[str] = None,
                 dialect: str = "sqlite", workspace=None, 
                 config: Optional[Config] = None, next_rule: Optional[PreCheckingRule] = None, verbose=False):
        super().__init__(next_rule)
        self.schema = schema
        self.gold = gold
        self.pred = pred
        self.host_or_path = host_or_path
        self.database = database
        self.port = port
        self.username = username
        self.password = password
        self.dialect = dialect
        self.workspace = workspace
        self.verbose = verbose
        self.config = config or Config()
       
    
    def _check(self):
        sqls = {"gold": self.gold, "pred": self.pred}
        for tag, query in sqls.items():
            
            instance = Instance(self.schema, name = self.database, dialect= self.dialect, host_or_path= self.host_or_path, database= self.database + '_' + tag, port= self.port, username= self.username, password= self.password)
            query = preprocess_sql(query, instance, dialect= self.dialect)
            generator = DataGenerator(query, instance= instance, workspace= self.workspace, verbose= self.verbose, random_seed= self.config.seed)
            
            result_checking = ResultChecking(instance= instance, gold= self.gold, pred= self.pred, dialect= self.dialect, config= self.config)
            
            generator.speculative(query, early_stop= result_checking)
            # generator.generate(timeout= self.timeout, early_stop = self.early_stop)
            
            r = result_checking(instance)
            
            if r:
                return PreCheckResult(state= "NEQ")
            else:
                return PreCheckResult(state='unknown')
            
            if r.state == 'NEQ':
                return r
        
        return PreCheckResult(state= 'eq')
        
class Disprover:
    RULES = [
        AstChecking,
        SyntaxChecking,
    ]
    def __init__(self, schema: str, gold: str, pred: str, host_or_path: str, database: str, config: Optional[Config] = None,
                 port: Optional[int] = None, username: Optional[str] = None,  password: Optional[str] = None, 
                 dialect: str = "sqlite", workspace=None, name: Optional[str] = "default", verbose=False):
        self.schema = schema
        self.gold = gold
        self.pred = pred
        self.dialect = dialect
        self.name = name
        self.config = config or Config()
        self.host_or_path = host_or_path
        self.database = database
        self.port = port
        self.username = username
        self.password = password
        self.dialect = dialect
        self.workspace = workspace
        self.verbose = verbose
        self._chain = self.__pre_checking()
        
    
    def __pre_checking(self):
        _chain =  AstChecking(gold= self.gold, pred= self.pred, dialect= self.dialect)
        
        dbname = self.database + "_syntax"
        syntax_check = SyntaxChecking(schema= self.schema, gold= self.gold, host_or_path= self.host_or_path, database= dbname, port= self.port, username= self.username, password= self.password, pred= self.pred, dialect= self.dialect)
        
        execution_check = ExecutionChecking(schema = self.schema, gold= self.gold, host_or_path= self.host_or_path, database= self.database, port= self.port, username= self.username, password= self.password, pred= self.pred, dialect= self.dialect, workspace= self.workspace, verbose= self.verbose, config= self.config)
        _chain.set_next(syntax_check).set_next(execution_check)
        return _chain
        
    def verify(self):
        return self._chain.check()