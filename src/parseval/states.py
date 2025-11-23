from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Union
from enum import Enum


class ParSEvalState(Enum):
    INITIAL = "initial"
    PARSING = "parsing"
    VALIDATING = "validating"
    TRANSFORMING = "transforming"
    COMPLETED = "completed"
    ERROR = "error"


from typing import Generic, TypeVar, Union, Any, NamedTuple

T = TypeVar("T")  # Type for the success value
E = TypeVar("E")  # Type for the error value


# class Success(NamedTuple, Generic[T]):
#     """Represents a successful operation result."""

#     value: T


# class Failure(NamedTuple, Generic[E]):
#     """Represents a failed operation result, containing a specific error."""

#     error: E


# # This is your final, explicit Result type
# Result = Union[Success[T], Failure[E]]


class ParSEvalError(Exception):
    """Base exception for ParSEval-related errors.

    This exception is raised when there are general errors related to
    the ParSEval process.
    """

    pass


class SchemaException(ParSEvalError):
    """Base exception for schema-related errors.

    This exception is raised when there are issues related to the schema,
    such as missing columns, mismatched data types, or invalid schema definitions.
    """

    pass


class SyntaxException(ParSEvalError):
    """Base exception for syntax-related errors.

    This exception is raised when there are syntax errors in the input,
    such as invalid query syntax or malformed expressions.
    """

    pass


class ValidationException(ParSEvalError):
    """Base exception for validation-related errors.

    This exception is raised when validation checks fail, such as
    constraints on data integrity, uniqueness, or nullability.
    """

    pass


# @dataclass
# class State:
#     """Represents the state of an operation or process.

#     Attributes:
#         name: The name of the state.
#         metadata: Optional metadata associated with the state.
#     """

#     name: str
#     metadata: Optional[Dict[str, Any]] = None


# @dataclass
# class Transition:
#     """Represents a transition between states.

#     Attributes:
#         from_state: The starting state of the transition.
#         to_state: The ending state of the transition.
#         condition: An optional condition that must be met for the transition.
#     """

#     from_state: State
#     to_state: State
#     condition: Optional[str] = None


# class StateMachine:
#     """Represents a state machine for managing transitions between states.

#     Attributes:
#         states: A list of all states in the state machine.
#         transitions: A list of all transitions between states.
#         current_state: The current state of the state machine.
#     """

#     def __init__(
#         self, states: List[State], transitions: List[Transition], initial_state: State
#     ):
#         self.states = states
#         self.transitions = transitions
#         self.current_state = initial_state

#     def transition_to(self, next_state: State) -> None:
#         """Transition to the specified state if a valid transition exists.

#         Args:
#             next_state: The state to transition to.

#         Raises:
#             ValidationException: If the transition is not valid.
#         """
#         valid_transition = any(
#             t
#             for t in self.transitions
#             if t.from_state == self.current_state and t.to_state == next_state
#         )
#         if not valid_transition:
#             raise ValidationException(
#                 f"Invalid transition from {self.current_state.name} to {next_state.name}."
#             )
#         self.current_state = next_state

#     def get_possible_transitions(self) -> List[State]:
#         """Get a list of possible states to transition to from the current state.

#         Returns:
#             A list of states that can be transitioned to from the current state.
#         """
#         return [
#             t.to_state for t in self.transitions if t.from_state == self.current_state
#         ]
