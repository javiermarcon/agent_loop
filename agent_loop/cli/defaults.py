"""Stable default values for the agent-loop command-line contract."""

CLI_DESCRIPTION = (
    "Loop local agente -> stage -> pre-commit -> quality checks.\n"
    "\n"
    "Indicá exactamente una fuente de requerimiento: --requirement,\n"
    "--requirement-file, --notion-url o --requirement-clipboard."
)
CLI_EPILOG = (
    "Ejemplos de uso:\n"
    '  python3 scripts/agent_loop.py --requirement "Implementá X"\n'
    "  python3 scripts/agent_loop.py --requirement-file prompts/issue.md\n"
    '  python3 scripts/agent_loop.py --notion-url "https://www.notion.so/..."\n'
    "  python3 scripts/agent_loop.py --requirement-clipboard\n"
    "\n"
    "Revisión de plan compuesto (sin agente ni gates):\n"
    "  python3 scripts/agent_loop.py --multi-requirement --dry-run \\\n"
    "    --requirement-file prompts/plan.md\n"
    "\n"
    "Guía operativa: scripts/README_AGENT_LOOP.md"
)
CLI_HELP = {
    "clipboard": "Lee el requerimiento desde el portapapeles del host.",
    "context_file": "Archivo contextual que mejora el plan pero no produce unidades.",
    "dry_enhance": (
        "Lee el requerimiento, aplica --enhance-requirement si fue indicado, "
        "imprime el resultado y termina sin ejecutar el agente ni los gates."
    ),
    "dry_run": "Con --multi-requirement, muestra el plan interpretado sin agente, estado, Git ni quality gates.",
    "enhance": "Convierte el requerimiento Markdown/texto en un prompt efectivo antes de enviarlo al agente.",
    "multi": "Segmenta y ejecuta documentos compuestos una unidad por vez.",
    "notion": "URL de página Notion para descargar el Markdown; requiere NOTION_API_KEY.",
    "plan_overrides": "Override declarativo JSON aplicado antes del lint final.",
    "requirement": (
        "Requerimiento inline. Debe usarse exactamente una fuente: "
        "texto, --requirement-file, --notion-url o --requirement-clipboard."
    ),
    "requirement_file": (
        "Archivo de requerimiento. En --multi-requirement puede repetirse "
        "para combinar especificaciones en el orden indicado."
    ),
    "requirement_folder": (
        "Carpeta de requerimientos textuales. Puede repetirse y habilita automáticamente --multi-requirement."
    ),
    "folder_recursive": (
        "Incluye subcarpetas seguras de cada --requirement-folder; por defecto sólo se leen archivos directos."
    ),
}

DEFAULT_MAX_ITERATIONS = 30
DEFAULT_MAX_PRECOMMIT_PASSES = 3
DEFAULT_MAX_AGENT_OUTPUT_CHARS = 2_000
DEFAULT_MAX_CHECK_OUTPUT_CHARS = 2_000
DEFAULT_MAX_ITERATION_REPOSITORY_CONTEXT_CHARS = 6_000
DEFAULT_MAX_GATE_RETRIES_PER_GATE = 3
DEFAULT_MAX_MODIFIED_FILES = 12
DEFAULT_MAX_MODIFIED_MODULES = 4
DEFAULT_MAX_NEW_FILES_PER_FIX_ITERATION = 4
DEFAULT_AGENT_TIMEOUT_SECONDS = 60 * 60
DEFAULT_PRECOMMIT_TIMEOUT_SECONDS = 60 * 60
DEFAULT_CHECK_TIMEOUT_SECONDS = 60 * 60 * 3
DEFAULT_NOTION_TIMEOUT_SECONDS = 30
