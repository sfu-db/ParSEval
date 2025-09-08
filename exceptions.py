
class UnSupportError(Exception):
    def __init__(self, message="encounter unsupport query feature"):
        super().__init__(message)



class SchemaError(Exception):

    def __init__(self, message = "SCHEMA ERROR") -> None:
        super().__init__(message)

class QuerySyntaxError(Exception):
    def __init__(self, message = "Queyr Syntax ERROR") -> None:
        super().__init__(message)

class UserDefineFunctionError(UnSupportError):
    def __init__(self, message = "Encounter unsupported user define function") -> None:
        super().__init__(message)



# class TimeoutError(Exception):
#     ...

def assert_state(state: str, error):
    state_err_mappings = {
        "SYNTAX_ERROR": QuerySyntaxError,
        'SCHEMA_ERROR': SchemaError,
        'USER_DEFINE_FUNCTION_ERROR': UserDefineFunctionError
    }
    
    if state.upper() in state_err_mappings:
        raise state_err_mappings[state.upper()](message = error)
    
