"""Unit tests for S16 AST inventory detectors (import/use/invoke separation)."""

from __future__ import annotations

import ast

import pytest

from tests.agent_loop.application.execution_request_ast_inventory import calls_agent_execution_request
from tests.agent_loop.application.execution_request_ast_inventory import import_access_prefix
from tests.agent_loop.application.execution_request_ast_inventory import import_bound_name
from tests.agent_loop.application.execution_request_ast_inventory import imported_request_name_aliases
from tests.agent_loop.application.execution_request_ast_inventory import imported_runtime_module_prefixes
from tests.agent_loop.application.execution_request_ast_inventory import parse_source
from tests.agent_loop.application.execution_request_ast_inventory import source_imports_execution_request_builder
from tests.agent_loop.application.execution_request_ast_inventory import source_invokes_build_execution_request
from tests.agent_loop.application.execution_request_ast_inventory import source_references_builder_symbols

_APP_PKG = "agent_loop.application"
_BUILDER_MOD = "agent_loop.application.execution_request_builder"
_RUNTIME_MOD = "agent_loop.ports.agent_runtime"


def _alias(name: str, asname: str | None = None) -> ast.alias:
    return ast.alias(name=name, asname=asname)


@pytest.mark.parametrize(
    ("name", "asname", "bound", "access"),
    [
        (_BUILDER_MOD, None, "agent_loop", _BUILDER_MOD),
        (_BUILDER_MOD, "erb", "erb", "erb"),
        (_RUNTIME_MOD, None, "agent_loop", _RUNTIME_MOD),
        (_RUNTIME_MOD, "runtime", "runtime", "runtime"),
    ],
)
def test_import_binding_respects_python_semantics(
    name: str,
    asname: str | None,
    bound: str,
    access: str,
) -> None:
    """``import a.b.c`` binds ``a``; ``as`` binds the alias; access uses full path or alias."""
    alias = _alias(name, asname)
    assert import_bound_name(alias) == bound
    assert import_access_prefix(alias) == access


@pytest.mark.parametrize(
    ("source", "package", "expected"),
    [
        (
            f"from {_BUILDER_MOD} import build_execution_request\n",
            _APP_PKG,
            (True, False, False),
        ),
        (
            f"from {_BUILDER_MOD} import build_execution_request\nbuild_execution_request\n",
            _APP_PKG,
            (True, True, False),
        ),
        (
            (
                f"from {_BUILDER_MOD} import build_execution_request\n"
                "build_execution_request(item, completed_ids=(), options=opts)\n"
            ),
            _APP_PKG,
            (True, True, True),
        ),
        (
            (
                f"from {_BUILDER_MOD} import (\n"
                "    build_execution_request as build_request,\n"
                ")\n"
                "build_request(item, completed_ids=(), options=opts)\n"
            ),
            _APP_PKG,
            (True, True, True),
        ),
        (
            (
                "from .execution_request_builder import build_execution_request\n"
                "build_execution_request(item, completed_ids=(), options=opts)\n"
            ),
            _APP_PKG,
            (True, True, True),
        ),
        (
            "from . import execution_request_builder\n",
            _APP_PKG,
            (True, False, False),
        ),
        (
            (
                "from . import execution_request_builder\n"
                "execution_request_builder.build_execution_request("
                "item, completed_ids=(), options=opts)\n"
            ),
            _APP_PKG,
            (True, True, True),
        ),
        (
            f"import {_BUILDER_MOD}\n{_BUILDER_MOD}.build_execution_request(item, completed_ids=(), options=opts)\n",
            _APP_PKG,
            (True, True, True),
        ),
        (
            f"import {_BUILDER_MOD} as erb\nerb.build_execution_request(item, completed_ids=(), options=opts)\n",
            _APP_PKG,
            (True, True, True),
        ),
        (
            (
                "from agent_loop.application import execution_request_builder\n"
                "execution_request_builder.build_execution_request("
                "item, completed_ids=(), options=opts)\n"
            ),
            _APP_PKG,
            (True, True, True),
        ),
        (
            f"from {_BUILDER_MOD} import ExecutionRequestOptions\nExecutionRequestOptions\n",
            _APP_PKG,
            (True, True, False),
        ),
        (
            "x = 1\n",
            _APP_PKG,
            (False, False, False),
        ),
    ],
)
def test_builder_import_reference_invoke_matrix(
    source: str,
    package: str,
    expected: tuple[bool, bool, bool],
) -> None:
    """Import, symbol reference, and invocation are reported independently."""
    expect_imports, expect_references, expect_invokes = expected
    assert source_imports_execution_request_builder(source, package=package) is expect_imports
    assert source_references_builder_symbols(source, package=package) is expect_references
    assert source_invokes_build_execution_request(source, package=package) is expect_invokes


@pytest.mark.parametrize(
    ("source", "package"),
    [
        (
            "from . import execution_request_builder\nexecution_request_builder\n",
            _APP_PKG,
        ),
        (
            f"import {_BUILDER_MOD} as erb\nerb\n",
            _APP_PKG,
        ),
        (
            f"import {_BUILDER_MOD}\nagent_loop\n",
            _APP_PKG,
        ),
    ],
)
def test_bare_module_name_is_not_symbol_reference(source: str, package: str) -> None:
    """Loading an imported module name alone is not a builder-symbol reference."""
    assert source_imports_execution_request_builder(source, package=package) is True
    assert source_references_builder_symbols(source, package=package) is False
    assert source_invokes_build_execution_request(source, package=package) is False


@pytest.mark.parametrize(
    ("source", "package"),
    [
        (
            "from . import execution_request_builder\nexecution_request_builder.build_execution_request\n",
            _APP_PKG,
        ),
        (
            f"import {_BUILDER_MOD} as erb\nerb.ExecutionRequestOptions\n",
            _APP_PKG,
        ),
    ],
)
def test_module_attribute_access_to_known_symbols_counts_as_reference(
    source: str,
    package: str,
) -> None:
    """Attribute access to known builder symbols via module prefixes is a reference."""
    assert source_imports_execution_request_builder(source, package=package) is True
    assert source_references_builder_symbols(source, package=package) is True
    assert source_invokes_build_execution_request(source, package=package) is False


@pytest.mark.parametrize(
    ("source", "package", "expected"),
    [
        (
            f"from {_BUILDER_MOD} import *\n",
            _APP_PKG,
            (True, False, False),
        ),
        (
            "from .execution_request_builder import *\n",
            _APP_PKG,
            (True, False, False),
        ),
        (
            f"from {_BUILDER_MOD} import *\nbuild_execution_request\n",
            _APP_PKG,
            (True, False, False),
        ),
        (
            f"from {_BUILDER_MOD} import *\nbuild_execution_request(item, completed_ids=(), options=opts)\n",
            _APP_PKG,
            (True, False, False),
        ),
        (
            "from some.unrelated.module import *\n",
            _APP_PKG,
            (False, False, False),
        ),
    ],
)
def test_builder_wildcard_import_without_inferred_symbols(
    source: str,
    package: str,
    expected: tuple[bool, bool, bool],
) -> None:
    """Builder wildcards count as imports; symbols/invokes are not inferred."""
    expect_imports, expect_references, expect_invokes = expected
    assert source_imports_execution_request_builder(source, package=package) is expect_imports
    assert source_references_builder_symbols(source, package=package) is expect_references
    assert source_invokes_build_execution_request(source, package=package) is expect_invokes


@pytest.mark.parametrize(
    ("source", "package"),
    [
        (
            "Request = object\nRequest = build_execution_request\n",
            _APP_PKG,
        ),
        (
            f"mod = __import__('{_BUILDER_MOD}')\n",
            _APP_PKG,
        ),
        (
            "# build_execution_request mentioned only in a comment\n",
            _APP_PKG,
        ),
    ],
)
def test_builder_detectors_documented_limitations(source: str, package: str) -> None:
    """Assignment aliases, dynamic imports, and comments are not treated as imports."""
    assert not source_imports_execution_request_builder(source, package=package)
    assert not source_references_builder_symbols(source, package=package)
    assert not source_invokes_build_execution_request(source, package=package)


@pytest.mark.parametrize(
    ("source", "package"),
    [
        (
            f"from {_RUNTIME_MOD} import AgentExecutionRequest\nAgentExecutionRequest(task='t')\n",
            _APP_PKG,
        ),
        (
            f"from {_RUNTIME_MOD} import AgentExecutionRequest as Request\nRequest(task='t')\n",
            _APP_PKG,
        ),
        (
            f"import {_RUNTIME_MOD} as agent_runtime\nagent_runtime.AgentExecutionRequest(task='t')\n",
            _APP_PKG,
        ),
        (
            "from agent_loop.ports import agent_runtime\nagent_runtime.AgentExecutionRequest(task='t')\n",
            _APP_PKG,
        ),
        (
            "from ..ports.agent_runtime import AgentExecutionRequest as Req\nReq(task='t')\n",
            _APP_PKG,
        ),
        (
            f"import {_RUNTIME_MOD}\n{_RUNTIME_MOD}.AgentExecutionRequest(task='t')\n",
            _APP_PKG,
        ),
    ],
)
def test_request_ctor_detector_positive_cases(source: str, package: str) -> None:
    """Detector recognizes direct imports and recognized-module attribute constructors."""
    tree = parse_source(source)
    assert calls_agent_execution_request(tree, package=package)


@pytest.mark.parametrize(
    ("source", "package"),
    [
        ("something.AgentExecutionRequest(task='t')\n", _APP_PKG),
        ("AgentExecutionRequest(task='t')\n", _APP_PKG),
        ("unrelated.ports.agent_runtime.AgentExecutionRequest(task='t')\n", _APP_PKG),
    ],
)
def test_request_ctor_detector_rejects_unknown_provenance(source: str, package: str) -> None:
    """Arbitrary attribute bases and bare names without import are not verified."""
    tree = parse_source(source)
    assert not calls_agent_execution_request(tree, package=package)


@pytest.mark.parametrize(
    ("source", "package"),
    [
        (
            f"from {_RUNTIME_MOD} import AgentExecutionRequest\nRequest = AgentExecutionRequest\nRequest(task='t')\n",
            _APP_PKG,
        ),
        (
            f"import {_RUNTIME_MOD} as agent_runtime\ngetattr(agent_runtime, 'AgentExecutionRequest')(task='t')\n",
            _APP_PKG,
        ),
        (
            "eval('AgentExecutionRequest(task=\"t\")')\n",
            _APP_PKG,
        ),
    ],
)
def test_request_ctor_detector_documented_limitations(source: str, package: str) -> None:
    """Assignment aliases, getattr, and eval are not detected."""
    tree = parse_source(source)
    assert not calls_agent_execution_request(tree, package=package)


def test_request_ctor_detector_does_not_expand_wildcard_imports() -> None:
    """Wildcard imports are not expanded into alias maps (limitation)."""
    source = f"from {_RUNTIME_MOD} import *\nAliased = AgentExecutionRequest\n"
    tree = parse_source(source)
    aliases = imported_request_name_aliases(tree, package=_APP_PKG)
    assert aliases == set()


def test_runtime_module_prefix_for_unaliased_absolute_import() -> None:
    """Unaliased ``import agent_loop.ports.agent_runtime`` uses the full access path."""
    source = f"import {_RUNTIME_MOD}\n"
    tree = parse_source(source)
    prefixes = imported_runtime_module_prefixes(tree, package=_APP_PKG)
    assert prefixes == {_RUNTIME_MOD}
    assert "agent_runtime" not in prefixes
