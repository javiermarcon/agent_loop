"""Build and parse the canonical agent-loop command-line contract."""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

from agent_loop.cli.defaults import CLI_DESCRIPTION
from agent_loop.cli.defaults import CLI_EPILOG
from agent_loop.cli.defaults import CLI_HELP
from agent_loop.cli.defaults import DEFAULT_AGENT_TIMEOUT_SECONDS
from agent_loop.cli.defaults import DEFAULT_CHECK_TIMEOUT_SECONDS
from agent_loop.cli.defaults import DEFAULT_MAX_AGENT_OUTPUT_CHARS
from agent_loop.cli.defaults import DEFAULT_MAX_CHECK_OUTPUT_CHARS
from agent_loop.cli.defaults import DEFAULT_MAX_GATE_RETRIES_PER_GATE
from agent_loop.cli.defaults import DEFAULT_MAX_ITERATION_REPOSITORY_CONTEXT_CHARS
from agent_loop.cli.defaults import DEFAULT_MAX_ITERATIONS
from agent_loop.cli.defaults import DEFAULT_MAX_MODIFIED_FILES
from agent_loop.cli.defaults import DEFAULT_MAX_MODIFIED_MODULES
from agent_loop.cli.defaults import DEFAULT_MAX_NEW_FILES_PER_FIX_ITERATION
from agent_loop.cli.defaults import DEFAULT_MAX_PRECOMMIT_PASSES
from agent_loop.cli.defaults import DEFAULT_NOTION_TIMEOUT_SECONDS
from agent_loop.cli.defaults import DEFAULT_PRECOMMIT_TIMEOUT_SECONDS
from agent_loop.cli.option_provenance import attach_cli_option_provenance


def _add_requirement_source_arguments(parser: argparse.ArgumentParser) -> None:
    """Register explicit file and deterministic folder input sources."""
    parser.add_argument("--requirement-file", action="append", help=CLI_HELP["requirement_file"])
    parser.add_argument("--requirement-folder", action="append", help=CLI_HELP["requirement_folder"])
    parser.add_argument(
        "--requirement-folder-recursive",
        action="store_true",
        help=CLI_HELP["folder_recursive"],
    )


def _add_multi_requirement_policy_arguments(parser: argparse.ArgumentParser) -> None:
    """Register source-role and execution-policy controls."""
    parser.add_argument("--requirement-context-file", action="append", help=CLI_HELP["context_file"])
    parser.add_argument(
        "--multi-requirement-audience",
        action="append",
        choices=("backend", "frontend", "mobile", "qa", "devops", "unknown", "all"),
        default=None,
        help="Audiencia elegible; puede repetirse. Default: all.",
    )
    parser.add_argument(
        "--multi-requirement-manual-policy",
        choices=("pause", "skip"),
        default="pause",
        help="Política para pasos manuales de audiencias seleccionadas.",
    )
    parser.add_argument(
        "--multi-requirement-validation-policy",
        choices=("run", "skip", "manual", "final-audit"),
        default="run",
        help="Política de ejecución para unidades de validación.",
    )
    parser.add_argument("--multi-requirement-plan-overrides", help=CLI_HELP["plan_overrides"])


def _add_requirement_arguments(parser: argparse.ArgumentParser) -> None:
    """Register inline, Notion, clipboard, and enhancement controls."""
    parser.add_argument("--requirement", help=CLI_HELP["requirement"])
    parser.add_argument("--notion-url", help=CLI_HELP["notion"])
    parser.add_argument("--requirement-clipboard", action="store_true", help=CLI_HELP["clipboard"])
    parser.add_argument("--enhance-requirement", action="store_true", help=CLI_HELP["enhance"])
    parser.add_argument("--dry-enhance", action="store_true", help=CLI_HELP["dry_enhance"])


def _add_multi_requirement_arguments(parser: argparse.ArgumentParser) -> None:
    """Register multi-run selection controls."""
    parser.add_argument("--multi-requirement", action="store_true", help=CLI_HELP["multi"])
    parser.add_argument("--dry-run", action="store_true", help=CLI_HELP["dry_run"])
    parser.add_argument("--multi-requirement-unit", help="Ejecuta una unidad exacta por ID.")
    parser.add_argument(
        "--multi-requirement-from-unit",
        help="Reanuda desde la unidad indicada, respetando dependencias.",
    )
    parser.add_argument("--multi-requirement-state", help="Path explícito del estado JSON reanudable.")


def _add_resume_cli_arguments(parser: argparse.ArgumentParser) -> None:
    """Register durable recovery controls."""
    parser.add_argument(
        "--resume-multi-requirement-run",
        help=(
            "Reanuda una corrida jerárquica por directorio o run_state.json; "
            "requiere las mismas fuentes para validar el fingerprint."
        ),
    )
    parser.add_argument(
        "--resume-run",
        dest="resume_multi_requirement_run",
        help="Alias corto para reanudar una corrida durable por ID o path.",
    )
    parser.add_argument(
        "--resume-last",
        action="store_true",
        help="Reanuda la corrida compatible más reciente del target.",
    )
    parser.add_argument(
        "--list-resumable-runs",
        action="store_true",
        help="Lista corridas reanudables del target y termina sin mutarlas.",
    )
    parser.add_argument(
        "--additional-iterations",
        type=int,
        default=0,
        help=("Al reanudar, agrega iteraciones de agente a la unidad activa cuando el presupuesto está agotado."),
    )


def _add_multi_requirement_budget_arguments(parser: argparse.ArgumentParser) -> None:
    """Register multi-run continuation and budget controls."""
    parser.add_argument(
        "--multi-requirement-auto-continue",
        action="store_true",
        help=("Permite continuar tras gates verdes y revisión humana explícita; no omite la decisión de commit."),
    )
    parser.add_argument(
        "--multi-requirement-max-units",
        type=int,
        default=1,
        help="Máximo acotado de unidades ejecutadas en esta corrida.",
    )
    parser.add_argument(
        "--multi-requirement-max-total-modified-files",
        type=int,
        default=DEFAULT_MAX_MODIFIED_FILES,
        help="Presupuesto global de archivos distintos para el plan completo.",
    )
    parser.add_argument(
        "--allow-cross-worktree-units",
        action="store_true",
        help=("Habilita seleccionar unidades marcadas cross-worktree; no cambia de worktree automáticamente."),
    )


def _add_repository_and_instruction_arguments(parser: argparse.ArgumentParser) -> None:
    """Register repository context, scope, and instruction controls."""
    parser.add_argument(
        "--enhancement-repo-root",
        help=("Root compatible usado como target si --repo-root no está presente y como fuente de enhancement."),
    )
    parser.add_argument("--repo-root", help="Root general del repositorio target que Codex modifica y valida.")
    parser.add_argument(
        "--auto-enhancement-repo-index",
        action="store_true",
        help="Usa el repo root validado del loop para enriquecer el prompt.",
    )
    parser.add_argument(
        "--no-enhancement-repo-index",
        action="store_true",
        help="Desactiva explícitamente el índice durante el enhancement.",
    )
    parser.add_argument(
        "--no-iteration-repo-context",
        action="store_true",
        help="Desactiva contexto repository-aware en prompts de reparación.",
    )
    parser.add_argument(
        "--max-iteration-repo-context-chars",
        type=int,
        default=DEFAULT_MAX_ITERATION_REPOSITORY_CONTEXT_CHARS,
        help="Límite independiente del contexto repository-aware iterativo.",
    )
    parser.add_argument(
        "--enforce-scope",
        action="store_true",
        help="Aborta antes de los gates si el agente modifica rutas no permitidas.",
    )
    parser.add_argument(
        "--scope-path",
        action="append",
        default=[],
        help="Ruta permitida; puede repetirse y se usa con --enforce-scope.",
    )


def _add_runtime_context_arguments(parser: argparse.ArgumentParser) -> None:
    """Register runtime instruction and diagnostic controls."""
    parser.add_argument(
        "--extra-instruction",
        action="append",
        default=[],
        help="Texto operativo global; puede repetirse y nunca altera gates.",
    )
    parser.add_argument(
        "--extra-instructions-file",
        action="append",
        default=[],
        help="Archivo UTF-8 de instrucciones globales; puede repetirse.",
    )
    parser.add_argument(
        "--unit-extra-instruction",
        action="append",
        nargs=2,
        metavar=("UNIT_ID", "TEXT"),
        default=[],
        help="Instrucción operativa limitada a una unidad estructural.",
    )
    parser.add_argument(
        "--instructions-config",
        help="Configuración YAML/JSON con instrucciones globales y por unidad.",
    )
    parser.add_argument(
        "--known-quality-diagnostic",
        action="append",
        default=[],
        help="YAML/JSON con diagnósticos conocidos, acciones y expiración.",
    )


def _add_execution_arguments(parser: argparse.ArgumentParser) -> None:
    """Register iteration, output, timeout, and commit controls."""
    parser.add_argument(
        "--notion-timeout-seconds",
        type=int,
        default=DEFAULT_NOTION_TIMEOUT_SECONDS,
        help=(f"Timeout en segundos de la descarga Notion. Default: {DEFAULT_NOTION_TIMEOUT_SECONDS}."),
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=DEFAULT_MAX_ITERATIONS,
        help=(f"Máximo de iteraciones agente→gates del loop. Default: {DEFAULT_MAX_ITERATIONS}."),
    )
    parser.add_argument(
        "--gate-governor",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Clasifica gates y limita retries/expansión antes de reinvocar IA.",
    )
    parser.add_argument(
        "--max-gate-retries-per-gate",
        type=int,
        default=DEFAULT_MAX_GATE_RETRIES_PER_GATE,
        help=("Umbral global legacy; por defecto escala el diagnóstico y no detiene un fallo recuperable."),
    )
    parser.add_argument(
        "--gate-retry-limit",
        action="append",
        default=[],
        metavar="GATE=N|none",
        help="Override repetible por gate; none hereda sólo el presupuesto global.",
    )
    parser.add_argument(
        "--gate-retry-policy",
        action="append",
        default=["all=ask"],
        metavar="GATE=POLICY",
        help=("Policy por gate; all=ask es el default antes de detener: inherit_global, escalate, pause, stop o ask."),
    )
    parser.add_argument(
        "--max-modified-files",
        type=int,
        default=DEFAULT_MAX_MODIFIED_FILES,
        help=(f"Máximo de archivos modificados permitidos. Default: {DEFAULT_MAX_MODIFIED_FILES}."),
    )
    parser.add_argument(
        "--max-modified-modules",
        type=int,
        default=DEFAULT_MAX_MODIFIED_MODULES,
        help=(f"Máximo de módulos lógicos modificados. Default: {DEFAULT_MAX_MODIFIED_MODULES}."),
    )
    parser.add_argument(
        "--max-new-files-per-fix-iteration",
        type=int,
        default=DEFAULT_MAX_NEW_FILES_PER_FIX_ITERATION,
        help=(
            "Máximo de archivos nuevos por iteración de corrección. "
            f"Default: {DEFAULT_MAX_NEW_FILES_PER_FIX_ITERATION}."
        ),
    )
    parser.add_argument(
        "--max-precommit-passes",
        type=int,
        default=DEFAULT_MAX_PRECOMMIT_PASSES,
        help=(f"Reintentos máximos de pre-commit cuando modifica archivos. Default: {DEFAULT_MAX_PRECOMMIT_PASSES}."),
    )
    parser.add_argument(
        "--max-agent-output-chars",
        type=int,
        default=DEFAULT_MAX_AGENT_OUTPUT_CHARS,
        help=(
            f"Chars de salida del agente a mostrar cuando está truncada. Default: {DEFAULT_MAX_AGENT_OUTPUT_CHARS}."
        ),
    )
    parser.add_argument(
        "--max-check-output-chars",
        type=int,
        default=DEFAULT_MAX_CHECK_OUTPUT_CHARS,
        help=(
            f"Chars de fallo de gate a incluir en el prompt de corrección. Default: {DEFAULT_MAX_CHECK_OUTPUT_CHARS}."
        ),
    )
    parser.add_argument(
        "--show-full-agent-output",
        action="store_true",
        help=("Permite que el agente responda con salida completa y la imprime sin truncar en consola."),
    )
    parser.add_argument(
        "--no-stream-checks",
        action="store_true",
        help=("Desactiva la salida en vivo de pre-commit y scripts/run_quality_checks.py."),
    )
    parser.add_argument(
        "--stream-agent-output",
        action="store_true",
        help="Muestra la salida del agente en tiempo real mientras ejecuta.",
    )
    parser.add_argument(
        "--agent-timeout-seconds",
        type=int,
        default=DEFAULT_AGENT_TIMEOUT_SECONDS,
        help=(f"Timeout del agente en segundos. Default: {DEFAULT_AGENT_TIMEOUT_SECONDS}."),
    )
    parser.add_argument(
        "--precommit-timeout-seconds",
        type=int,
        default=DEFAULT_PRECOMMIT_TIMEOUT_SECONDS,
        help=(f"Timeout de pre-commit en segundos. Default: {DEFAULT_PRECOMMIT_TIMEOUT_SECONDS}."),
    )
    parser.add_argument(
        "--check-timeout-seconds",
        type=int,
        default=DEFAULT_CHECK_TIMEOUT_SECONDS,
        help=(f"Timeout de scripts/run_quality_checks.py en segundos. Default: {DEFAULT_CHECK_TIMEOUT_SECONDS}."),
    )
    parser.add_argument(
        "--log-dir",
        default=".agent-loop-logs",
        help="Directorio de prompts y logs (default: .agent-loop-logs).",
    )
    parser.add_argument(
        "--commit_msg",
        default=None,
        help=(
            "Si se provee, se pasa a run_quality_checks.py para realizar un "
            "git commit de los staged despues de behave y antes de act."
        ),
    )
    parser.add_argument(
        "--amend",
        action="store_true",
        help=(
            "Si se provee, se pasa a run_quality_checks.py para realizar "
            "git commit --amend despues de behave y antes de act."
        ),
    )


def build_argument_parser() -> argparse.ArgumentParser:
    """Build the complete agent-loop parser without consuming ``sys.argv``."""
    parser = argparse.ArgumentParser(
        description=CLI_DESCRIPTION,
        epilog=CLI_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--agent-command",
        default="codex exec",
        help="Comando del agente a ejecutar (default: 'codex exec').",
    )
    _add_requirement_source_arguments(parser)
    _add_multi_requirement_policy_arguments(parser)
    _add_requirement_arguments(parser)
    _add_multi_requirement_arguments(parser)
    _add_resume_cli_arguments(parser)
    _add_multi_requirement_budget_arguments(parser)
    _add_repository_and_instruction_arguments(parser)
    _add_runtime_context_arguments(parser)
    _add_execution_arguments(parser)
    return parser


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    """Parse an explicit argument vector and attach option provenance."""
    raw_argv = list(argv)
    parsed = build_argument_parser().parse_args(raw_argv)
    return attach_cli_option_provenance(parsed, raw_argv)
