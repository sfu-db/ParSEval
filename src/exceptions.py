

class Error(Exception):
    ...


class TimeOutException(Error):
    def __init__(self) -> None:
        self.message = 'Timeout'

class UnSupportError(Error):
    def __init__(self, message="encounter unsupport query feature"):
        super().__init__(message)

class SchemaError(Error):
    def __init__(self, message = "SCHEMA ERROR") -> None:
        super().__init__(message)

class QuerySyntaxError(Error):
    def __init__(self, message = "Queyr Syntax ERROR") -> None:
        super().__init__(message)

class UserDefineFunctionError(UnSupportError):
    def __init__(self, message = "Encounter unsupported user define function") -> None:
        super().__init__(message)

def assert_state(state: str, error):
    state_err_mappings = {
        "SYNTAX_ERROR": QuerySyntaxError,
        'SCHEMA_ERROR': SchemaError,
        'USER_DEFINE_FUNCTION_ERROR': UserDefineFunctionError
    }
    
    if state.upper() in state_err_mappings:
        raise state_err_mappings[state.upper()](message = error)
    

from types import FrameType, TracebackType
from typing import Union, Any, Type, Optional
import signal, traceback, sys

class SignalTimeout:
    """Execute a code block raising a timeout."""

    def __init__(self, timeout: Union[int, float]) -> None:
        """
        Constructor. Interrupt execution after `timeout` seconds.
        """
        self.timeout = timeout
        self.old_handler: Any = signal.SIG_DFL
        self.old_timeout = 0.0

    def __enter__(self) -> Any:
        """Begin of `with` block"""
        # Register timeout() as handler for signal 'SIGALRM'"
        self.old_handler = signal.signal(signal.SIGALRM, self.timeout_handler)
        self.old_timeout, _ = signal.setitimer(signal.ITIMER_REAL, self.timeout)
        return self

    def __exit__(self, exc_type: Type, exc_value: BaseException,
                 tb: TracebackType) -> None:
        """End of `with` block"""
        self.cancel()
        return  # re-raise exception, if any

    def cancel(self) -> None:
        """Cancel timeout"""
        signal.signal(signal.SIGALRM, self.old_handler)
        signal.setitimer(signal.ITIMER_REAL, self.old_timeout)

    def timeout_handler(self, signum: int, frame: Optional[FrameType]) -> None:
        """Handle timeout (SIGALRM) signal"""
        raise TimeoutError()
    

Timeout: Type[SignalTimeout] = SignalTimeout 

# if hasattr(signal, 'SIGALRM') else GenericTimeout


class ExpectTimeout(Timeout):
    """Execute a code block expecting (and catching) a timeout."""

    def __init__(self, timeout: Union[int, float],
                 print_traceback: bool = True, mute: bool = False):
        """
        Constructor. Interrupt execution after `seconds` seconds.
        If `print_traceback` is set (default), print a traceback to stderr.
        If `mute` is set (default: False), do not print anything.
        """
        super().__init__(timeout)

        self.print_traceback = print_traceback
        self.mute = mute

    def __exit__(self, exc_type: type,
                 exc_value: BaseException, tb: TracebackType) -> Optional[bool]:
        """End of `with` block"""

        super().__exit__(exc_type, exc_value, tb)

        if exc_type is None:
            return

        # An exception occurred
        if self.print_traceback:
            lines = ''.join(
                traceback.format_exception(
                    exc_type,
                    exc_value,
                    tb)).strip()
        else:
            lines = traceback.format_exception_only(
                exc_type, exc_value)[-1].strip()

        if not self.mute:
            print(lines, "(expected)", file=sys.stderr)

        return True  # Ignore exception
    

    