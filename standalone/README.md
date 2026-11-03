# Agent Loop — Autonomous Django Edition

Herramienta autónoma para ejecutar Agent Loop contra repositorios Django externos.
Este directorio es autocontenido: incluye el núcleo `agent_loop/`, el runtime
autónomo, la CLI, los checks y la documentación. No depende del checkout de
Shipii.

## 1. Requisitos

- Python 3.12 o superior.
- Git disponible en `PATH`.
- Un virtualenv propio para Agent Loop.
- Opcional: Docker con Compose, si el proyecto objetivo lo utiliza.
- El proyecto objetivo debe exponer un entorno ejecutable para sus
  herramientas: un virtualenv local o un servicio Docker Compose configurado y
  documentado.

## 2. Instalación

```bash
cd agent-loop-standalone
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
```

También se puede ejecutar sin instalar el paquete:

```bash
python run.py --help
```

O instalar el entry point:

```bash
pip install -e .
agent-loop --help
```

## 3. Uso

### Descubrir un proyecto

```bash
python run.py \
  --repo /ruta/al/proyecto-django \
  --requirement "Agregar endpoint de health check" \
  --discover-only
```

### Ver el prompt sin ejecutar nada

```bash
python run.py \
  --repo /ruta/al/proyecto-django \
  --requirement-file requerimiento.md \
  --dry-run
```

### Ejecutar sólo validaciones

```bash
python run.py \
  --repo /ruta/al/proyecto-django \
  --requirement "Validar el estado actual" \
  --validate-only \
  --tool pytest \
  --full
```

### Ejecutar un agente externo

```bash
python run.py \
  --repo /ruta/al/proyecto-django \
  --requirement "Implementar el requerimiento" \
  --agent-command "codex exec" \
  --max-iterations 3
```

El prompt se entrega por defecto según su tamaño: los prompts pequeños se
agregan como **último argumento** para conservar compatibilidad con CLIs que
sólo aceptan un mensaje posicional, y los prompts grandes se envían por
**stdin** para no exceder `MAX_ARG_STRLEN` (128 KiB por argumento en Linux).
`--prompt-transport` permite forzar `stdin`, `argument` o `auto`.

```bash
--agent-command "opencode run --auto" --prompt-transport stdin
```

Comandos que requieren un prompt posicional pueden seguir usándose:

```bash
--agent-command "cursor-agent --print" --prompt-transport argument
```

Si no se configura `--agent-command`, el modo normal falla de forma explícita
con estado `unavailable`; nunca simula una ejecución exitosa.

### Opciones principales

| Opción | Descripción |
|---|---|
| `--repo` | Ruta del repositorio Git objetivo. Obligatoria. |
| `--requirement` | Requerimiento inline. |
| `--requirement-file` | Archivo UTF-8 con el requerimiento; el reporte conserva su ruta de origen. |
| `--agent-command` | Comando externo del agente. |
| `--prompt-transport` | `auto` (stdin para prompts grandes), `stdin` o `argument`. Default: `auto`. |
| `--max-iterations` | Ciclos agente → validación. Default: 3. |
| `--timeout-seconds` | Timeout por comando. Default: 3600. |
| `--dry-run` | Descubre y muestra el prompt, sin ejecutar. |
| `--discover-only` | Sólo descubre y reporta. |
| `--validate-only` | Ejecuta validaciones sin agente. |
| `--tool` | Limita a una herramienta; puede repetirse. |
| `--full` | Validaciones completas en lugar de focalizadas. |
| `--json` | Reporte JSON. |
| `--output` | Escribe el reporte en un archivo. |
| `--env KEY=VALUE` | Variable explícita para el proceso objetivo. |
| `--target-path` | Ruta objetivo para resolver instrucciones y delimitar el alcance auditado de cambios. |
| `--log-dir` | Guarda `prompt.md` y `result.json` por corrida. |

## 4. Descubrimiento

### Instrucciones

- `AGENTS.md` en la raíz y en subdirectorios.
- `README.md`.
- Documentos de arquitectura, testing, estilo y contribución.
- Resolución por alcance: para un archivo, se incluyen las instrucciones raíz
  y la cadena de `AGENTS.md` de sus directorios ancestros.

Las instrucciones del proyecto objetivo gobiernan el desarrollo, pero **no
pueden anular** las políticas de seguridad internas de Agent Loop.

### Proyecto Django

Se detectan, sin importar el proyecto:

- `manage.py`, `DJANGO_SETTINGS_MODULE` (literal, variable o rama de
  `manage.py` resuelta con evidencia estática del runtime).
- Archivos de settings, incluidos paquetes `settings/` y sus imports locales.
- `INSTALLED_APPS` (listas concatenadas, `+=` e imports de settings).
- Clasificación de apps: proyecto, terceros y framework.
- Versión de Python (`.python-version`, `runtime.txt`, `pyproject.toml`).
- Versión de Django en archivos de dependencias.
- Archivos de dependencias y locks.
- Virtualenv (`.venv`, `venv`, `env`, `.virtualenv`).
- Archivos Compose estándar y variantes por entorno
  (`docker-compose.local.yml`, `compose.ci.yaml`, overrides), con selección
  respaldada por `justfile`, `Makefile`, scripts, CI y documentación.
- Servicio Compose de Django y directorio de trabajo efectivo del contenedor.
- Comandos oficiales del proyecto documentados para pytest, mypy, Behave, etc.

### Herramientas

Se detectan y clasifican en cuatro estados:

| Estado | Significado |
|---|---|
| `configured_available` | Configurada y verificada en el virtualenv objetivo o dentro del servicio Compose. |
| `configured_unavailable` | Configurada, pero la herramienta no está instalada en el entorno verificado. |
| `not_configured` | El proyecto no la configura. |
| `not_required` | Hay configuración residual, pero el proyecto no instala ni invoca la herramienta. |
| `unknown` | No se pudo verificar (por ejemplo, contenedor detenido). |

Herramientas contempladas: Git, pre-commit, Ruff, Mypy, Pylint, Bandit,
pytest, Behave y Docker Compose. Git se descubre y registra como capacidad de
solo lectura; su adaptador únicamente ejecuta `git status`. No se cuenta como
validación de calidad por defecto, pero puede solicitarse con `--tool git`.

**Nunca** se usa el intérprete de Agent Loop para ejecutar herramientas del
proyecto objetivo. Si no hay virtualenv local pero el proyecto configura Docker
Compose, Agent Loop verifica el módulo dentro del servicio y ejecuta las
validaciones con `docker compose -p <proyecto> -f <archivo> exec -T <servicio>`.
Si el servicio no está disponible, el estado es `unknown`, nunca
`configured_available` por inferencia.

### Baseline y auditoría Git

Antes de ejecutar el agente se captura un baseline con:

- HEAD, rama y estado inicial.
- Archivos staged, unstaged, untracked y eliminados.
- Hashes SHA-256 de los archivos ya modificados.
- Identidad del blob de índice para cada path ya modificado, de modo que un
  `git add` o `git restore --staged` posterior al baseline también se detecte.
- Reflog suficiente para detectar un HEAD que cambia y vuelve a su valor original.

Al finalizar se compara el estado final contra ese baseline y se clasifica cada
cambio como:

- `preexisting`: ya existía y no cambió, incluidas las eliminaciones previas.
- `execution`: apareció o cambió durante la ventana del agente.
- `validation`: apareció o cambió durante la ventana de un validador.
- `undetermined`: no puede atribuirse con certeza.

Se conserva el baseline original y se agregan ventanas separadas para cada
ejecución del agente y cada pasada de validación. Si HEAD o la rama cambian, se
inspecciona el rango de commits y sus paths. Un commit no se atribuye
automáticamente al agente: sus cambios quedan `undetermined` con una advertencia
explícita y el resultado global no puede quedar en `pass` sin revisión.

Una transición entre working tree e índice posterior al baseline se atribuye a
la ventana de ejecución aunque el contenido no cambie. Si el estado Git no se
puede leer, no se puede parsear o el comando falla, el resultado es
`unknown`: un error de auditoría nunca se convierte en `pass`.

Los cambios `execution` fuera de los `--target-path` indicados se reportan como
`unexpected_paths`. `--target-path` se normaliza a una ruta relativa POSIX dentro
del workspace; variantes como `./apps/x`, `apps/x/` o la ruta absoluta
equivalente producen el mismo alcance, mientras que rutas externas se rechazan.
Esta auditoría es post-hoc: un archivo creado y eliminado entre ambas capturas
no deja evidencia observable.

## 5. Ejecución y resultados

Cada ejecución registra:

- herramienta;
- comando;
- directorio de trabajo;
- intérprete;
- variables de entorno (sólo nombres);
- código de salida;
- stdout/stderr acotados;
- estado interpretado.

Estados posibles: `pass`, `fail`, `skipped`, `unavailable`, `unknown`.
Los `fail` se clasifican además como `implementation`, `usage`, `configuration`,
`infrastructure` o `execution`. Sólo `implementation` habilita un ciclo de
corrección: errores de uso de pytest, configuración, timeouts, intérprete
ausente o fallas internas detienen el loop y preservan el diagnóstico original.
Una herramienta que no pudo ejecutarse **nunca** se interpreta como `pass`.
Un reporte vacío o con todas las validaciones omitidas tampoco es `pass`:
se reporta como `unknown` o `skipped` según corresponda.

El reporte distingue:

- `status`: resultado operativo de la invocación.
- `validation_status`: resultado efectivo de las validaciones.

`--discover-only` y `--dry-run` pueden terminar operativamente en `pass` con
`validation_status: null`; eso no declara éxito de validación.

### Validaciones focalizadas

- pytest: un archivo de test cambiado se ejecuta directamente. Para módulos
  fuera de `apps/<app>/`, el mapeo usa `tests/**/test_<stem>.py`, un `tests.py`
  hermano o los `testpaths` configurados. Para módulos bajo `apps/<app>/` sólo
  se aceptan tests app-locales: un homónimo global único no demuestra que el
  test corresponda a esa app. Si hay correspondencias ambiguas o no hay mapeo
  determinista, se ejecuta la suite completa y el reporte lo indica. El código
  de salida 5 de pytest se interpreta como `skipped` (no se recolectaron tests),
  nunca como éxito.
- Ruff/Mypy/Bandit: usan los archivos Python cambiados; con `--full`, el
  proyecto completo.
- Pylint: sólo archivos Python cambiados.
- pre-commit: `--files` con los archivos cambiados; con `--full`,
  `--all-files`.
- Behave: features cambiadas o `features/`.
- Docker Compose: usa el comando descubierto (`docker compose` o
  `docker-compose`) y ejecuta `<comando> -f <base> [-f <override>] config --quiet`
  (validación de configuración; no levanta servicios). La selección entre
  variantes usa evidencia del proyecto, no orden alfabético; bases u overrides
  ambiguos producen un error explícito.
- Herramientas dentro de Compose: usan el prefijo
  `docker compose -p <proyecto> -f <archivo> exec -T <servicio>` descubierto y,
  cuando el proyecto los documenta, sus comandos oficiales.

## 6. Seguridad

- La ruta objetivo se normaliza con `resolve` y debe ser un worktree Git real;
  se aceptan repositorios convencionales y worktrees con `.git` como archivo.
- Toda ruta de escritura/lectura debe quedar dentro del repositorio autorizado.
- Los symlinks no pueden escapar del workspace; el recorrido resuelve enlaces,
  ignora enlaces rotos, evita ciclos y no atraviesa destinos externos. La
  excepción controlada es el intérprete del virtualenv, que puede apuntar a un
  Python externo únicamente cuando el workspace contiene su `pyvenv.cfg`. Los
  ejecutables de herramientas que resuelven fuera del workspace se rechazan y no
  se ejecutan, aunque estén enlazados desde `.venv/bin/`.
- El entorno del proceso objetivo se construye con una allowlist: no se heredan
  `PYTHONPATH`, `VIRTUAL_ENV`, `DJANGO_SETTINGS_MODULE`, variables `SHIPII_*`
  ni `AGENT_LOOP_*`.
- No se ejecuta con `shell=True`.
- No se ejecutan operaciones Git destructivas.
- Las operaciones internas de Git descartan variables `GIT_*` del proceso padre
  para impedir que `GIT_DIR`, `GIT_WORK_TREE`, el índice, los objetos o la
  configuración redirijan la inspección fuera del workspace autorizado.
- Las instrucciones del repositorio no pueden ampliar permisos ni comandos.
- Los secretos no se registran en logs.

## 7. Arquitectura

```text
agent_loop/
  domain/            contratos puros del control plane
  ports/             AgentRuntime y puertos
  application/       lifecycle y coordinación
  adapters/          discovery read-only de OpenCode/Claude
  project/           ProjectProfile / Skills (S20)
  autonomy/
    config.py        política interna de ejecución
    security.py      workspace, rutas y entorno aislado
    requirement.py   procedencia del requerimiento
    discovery/       instrucciones, Django, Git y herramientas
      git_attribution.py  baseline, ventanas y atribución de cambios
    tools/           contratos, command runner, registry, failure y adapters
    execution/       entorno objetivo, agente externo, runner y loop
    report.py        reporte estructurado y Markdown
    cli.py           entrypoint `agent-loop`
```

La capa `autonomy/` no importa `scripts`, `apps` ni Django. El núcleo
`agent_loop/domain` y `agent_loop/ports` permanece independiente.

### Extensibilidad futura

Para agregar otro ecosistema (Node.js, TypeScript, etc.) se deben agregar:

1. un descubrimiento específico del proyecto;
2. adapters de herramientas para ese ecosistema;
3. un registry que seleccione los adapters aplicables.

No se debe acoplar el núcleo a Ruff, pytest o Django. S21 no implementa otros
ecosistemas.

## 8. Duplicación y sincronización

`agent_loop/` en el repositorio Shipii es la **fuente canónica**.
`standalone/agent_loop/` es un **snapshot vendored** intencional para que esta
carpeta pueda copiarse fuera del checkout y ejecutarse de forma independiente.

El contrato completo está en `VENDORING.md`. La suite canónica verifica que el
snapshot no tenga archivos faltantes, archivos extra ni divergencias de bytes.
`standalone/checks/` es una copia transformada de los tests canónicos; no es
una segunda fuente de verdad. Un gate determinista comprueba el conjunto de
checks, `support.py` y la transformación documentada de cada test, y falla ante
cualquier drift no sincronizado.

## 9. Tests

```bash
pytest
```

Los checks cubren seguridad, instrucciones, descubrimiento Django, herramientas,
ejecución de comandos, entorno aislado, adapters, loop y CLI. También se validan
dos proyectos Django de ejemplo con configuraciones distintas en `fixtures/`.

## 10. Extracción

1. Copiar este directorio a otra ubicación.
2. Crear un repositorio Git independiente.
3. Crear un virtualenv.
4. `pip install -r requirements-dev.txt`.
5. Ejecutar `python run.py --help`.

No hay rutas absolutas, imports de Shipii, scripts externos ni dependencias del
checkout original.

## 11. Límites conocidos

- Sólo Django/Python. Otros ecosistemas quedan para etapas futuras.
- No hay publicación PyPI ni wheel/sdist obligatorios.
- La integración con OpenCode server pertenece a S22; aquí el agente es un
  comando externo explícito.
- Docker Compose se valida y también se usa para ejecutar las herramientas
  dentro del servicio cuando el proyecto lo configura y documenta.
- El agente externo es responsable de la planificación fina y de la edición;
  Agent Loop provee contexto, descubrimiento, ejecución y validación.
