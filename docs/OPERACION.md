# Operación del pipeline

Guía para quien mantiene este repositorio, con o sin un asistente de IA. Responde cuatro preguntas: qué corre solo,
dónde mirar cuando algo falla, cómo diagnosticarlo y qué se puede (y qué no) cambiar. Lo que ya está documentado en
otro lugar se enlaza en vez de repetirse: las series y sus fuentes en el [README](../README.md), cómo agregar un país y
las trampas de cada API en [AGREGAR_PAIS.md](AGREGAR_PAIS.md), los archivos publicados en
[CONTRATO_JSON.md](CONTRATO_JSON.md) y los significados en [MODELO_SEMANTICO.md](MODELO_SEMANTICO.md).

## 1. Qué corre solo y qué no

La actualización diaria no necesita a nadie. El código de producción no tiene dependencias (`dependencies = []` en
`pyproject.toml`) y ningún workflow ni módulo usa un modelo de IA: GitHub Actions ejecuta `python -m fxpipe` y el bot
`fx-bot` publica los cambios de `data/`.

| Workflow | Cuándo | Qué hace |
|---|---|---|
| `daily-update.yml` («Actualización diaria de tasas») | Lunes a viernes 19:30 UTC y todos los días 01:00 UTC; también a mano (`workflow_dispatch`, con `backfill_start` opcional) | Descarga, valida, calcula cruzadas y publica `data/` (solo hay commit si cambian los datos). Después verifica el estado de las series: si alguna no está en `ok`, abre o actualiza un issue `fx-alerta` que enlaza la corrida; si todas lo están, cierra los `fx-alerta` abiertos con un comentario que enlaza la corrida |
| `healthcheck.yml` | Lunes 13:00 UTC y a mano | Consulta los últimos 20 días de cada fuente y verifica su contrato. Si una fuente cambió su contrato, falla por un motivo operativo, no devuelve registros, o el último manifest no está en `ok`, abre o actualiza un issue `api-drift` (este **se cierra a mano**). Un fallo solo del respaldo (la SFC) no lo dispara |
| `ci.yml` («Pruebas») | Cada push a `main` (salvo cambios solo en `data/`) y cada PR | `pytest -q` en `ubuntu-24.04` (el runner de producción) y en `ubuntu-26.04` (canario, ver 5.8). Falla si la curaduría del modelo semántico tiene huecos |

Lo que el pipeline hace por sí mismo:

- **Una fuente caída nunca borra datos buenos.** La serie conserva su histórico y se marca `error:*` o `stale`.
- **Se recupera de caídas.** Cada corrida pide a cada proveedor datos desde 15 días antes de la última observación
  oficial de sus series (`incremental_lookback_days` en `config/sources.json`; si un proveedor tiene varias series, rige
  la más atrasada) hasta 7 días después de hoy (la TRM se publica con vigencia futura). Una caída, aunque dure más de 15
  días, se llena sola cuando la fuente vuelve.
- **Hace la carga inicial sola.** Una serie sin histórico se descarga desde `backfill_start` (en `config/sources.json`)
  en la primera corrida. Un `backfill` a mano sirve para recargar o corregir histórico (ver 5.2).
- **Usa el respaldo declarado**, si la serie lo tiene (la TRM: Banco de la República como principal y la
  Superintendencia Financiera en datos.gov.co como respaldo), y lo deja anotado en el manifest.

Lo que necesita a una persona:

- Un token vencido o revocado (el de la BDE de Chile vence al año).
- Una API que cambió su formato, su endpoint o su codificación (`error:contract`, issue `api-drift`).
- Una serie descontinuada o movida (`error:empty`, o `stale` que no se explica por un feriado).
- Un país o una serie nueva.
- Los avisos de GitHub sobre las acciones y el ejecutor (versiones de `actions/checkout` y `actions/setup-python`,
  imagen del runner): se atienden subiendo versiones en los tres workflows y comprobando una corrida manual (ver 5.8).
- Los workflows programados que GitHub desactiva en un repositorio público tras 60 días sin actividad
  ([documentación de GitHub](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/disable-and-enable-workflows)).
  No está documentado si los commits del bot cuentan como actividad: si un día no hay corridas, revisa la pestaña Actions.

## 2. Dónde mirar primero

1. **`data/manifest.json`**, el estado de la última corrida. Campos útiles: `overall_status` (`ok` o `degraded`),
   `series_status` (el estado de cada serie), `errors` (el motivo de cada error), `warnings` (avisos que no
   degradan; se conservan los últimos 20 por serie), `as_of` y `generated_at`, `config_version`.
2. **`python -m fxpipe check`**, que resume el manifest y sale con código 1 si alguna serie no está en `ok` o si no
   existe el manifest.
3. **El log de la corrida** en la pestaña Actions. El issue `fx-alerta` enlaza la corrida que lo abrió o lo actualizó.
4. **`python -m fxpipe healthcheck`**, para saber si el problema está en la fuente o en el pipeline: prueba cada
   API en vivo y no escribe en `data/`.

El manifest de una corrida con series degradadas **sí se publica** (el paso de publicar va antes del de verificar),
pero solo si algo cambió: se puede leer en el commit `datos: actualización AAAA-MM-DDTHH:MMZ` de la primera corrida con
ese fallo; si el fallo se repite igual, no hay commits nuevos. Si la corrida se interrumpe por una excepción antes de
publicar, el manifest no cambia: lee el log. Una alerta vieja suele estar resuelta: antes de actuar, compara con el
manifest actual y con las corridas posteriores.

`overall_status` del manifest cuenta todas las series; el de `latest.json` cuenta solo las `primary`.

El sitio del Banco Central do Brasil bloquea a los robots, y desde el entorno de un asistente de IA las peticiones a
Chile y Perú pueden recibir un 403 del proxy (ver [AGREGAR_PAIS.md](AGREGAR_PAIS.md)): la validación en vivo se hace
desde GitHub Actions, y un 403 obtenido fuera de ahí no prueba que la fuente haya fallado.

## 3. Estados y avisos

| Estado de una serie | Qué significa |
|---|---|
| `ok` | Descargada, dentro del rango plausible y con datos recientes |
| `stale` | La última observación oficial es más antigua que `stale_after_days` |
| `error:provider` | La fuente no respondió o rechazó la petición: secreto ausente o inválido, HTTP 401/403, fallo tras 4 intentos, firewall |
| `error:contract` | La respuesta cambió de forma: endpoint 404, no es JSON válido, codificación, campos que faltan |
| `error:validation` | Algún valor de la descarga cae fuera de `plausible_range`; mientras persista, no se incorpora ninguna observación nueva de esa serie |
| `error:empty` | La serie no tiene ninguna observación oficial |

Una cruzada hereda el estado `error:*` (`provider`, `contract` o `validation`) de su primer insumo roto, el numerador
antes que el denominador, y lo explica en `errors` con «insumo degradado: …». Si un insumo está `stale` o `error:empty`,
la cruzada no lo hereda: se evalúa aparte con su propio `stale_after_days` y sin mensaje en `errors`.

Los avisos (`warnings`) no degradan nada, pero hay que saber leerlos:

- `revisión AAAA-MM-DD: a -> b`: la fuente corrigió un valor que ya estaba guardado dentro de la ventana. Es normal.
  Si son muchos, investiga.
- `salto AAAA-MM-DD->AAAA-MM-DD`: variación diaria mayor que `max_daily_change_pct`. Los saltos históricos de ARS
  (p. ej. 2023-12-13, la devaluación) aparecen en el manifest mientras quepan en los últimos 20 avisos de la serie y son
  reales; los saltos recientes conviene confirmarlos con otra fuente (solo la TRM tiene una segunda fuente en el
  pipeline).
- `discrepancia con sfc_trm en N fechas`: la TRM del Banco de la República y la de la SFC difieren en alguna fecha.
- `sfc_trm tiene datos hasta X; la fuente principal hasta Y`: el respaldo va más adelantado que la principal.
- `fuente principal no disponible (…); se usó el respaldo …`: la serie quedó en `ok` con el respaldo; revisa por qué
  falló la principal.
- `verificación no disponible (…)`: no se pudo consultar la fuente de verificación; la serie sigue con la principal.
- `la fuente devolvió 0 registros en una ventana que antes tenía datos`: revisa la fuente antes de que pase a `stale`.
- `valores fuera de rango plausible: …` en una cruzada: es un aviso, no un error de la serie; revisa sus dos insumos.

## 4. Diagnóstico: síntoma, causa, acción

| Síntoma | Causa probable | Qué hacer |
|---|---|---|
| `error:provider` y «Falta el secreto X» | El secreto no existe o tiene otro nombre; o es un `token_env` nuevo que los workflows no exportan | Crearlo con el nombre exacto de `token_env` del proveedor en `config/sources.json` (Settings → Secrets and variables → Actions). Si el proveedor es nuevo, agregarlo también al bloque `env:` de `daily-update.yml` y de `healthcheck.yml`, que hoy solo exportan los tres secretos del apartado 5.4 |
| `error:provider` y «HTTP 401/403 (credenciales o permisos)» | Token inválido o revocado; en una fuente sin token, un bloqueo de la petición | Pedir un token nuevo en el portal de la fuente y reemplazar el secreto. Sin token, reintentar más tarde; si persiste, tratarlo como cambio de API |
| `error:provider` en Chile con `Codigo=-5` («Invalid username or password») | `BCCH_TOKEN` vencido o inválido | Renovarlo en la BDE (Mi Cuenta → Apikey Token) y reemplazar el secreto |
| `error:provider` y «Fallo tras 4 intentos» | Falla transitoria de la fuente (429, 5xx, tiempo de espera) | Cada petición ya se intenta 4 veces con espera creciente. Re-ejecutar más tarde; cuando la fuente vuelve, la serie se llena sola. Si dura varios días, revisar el estado del servicio de esa fuente |
| `error:provider` en Perú, «página de verificación del firewall Imperva Incapsula» | El firewall del BCRP responde 200 con una página HTML en lugar de JSON; el conector intenta 4 veces. También la devuelve una consulta mal formada (código de serie inexistente o rango de fechas invertido) | Si es esporádico, re-ejecutar más tarde. Si ocurre en todas las corridas y también en local, revisar `source_series_id` y el rango en lugar de esperar; si ocurre solo desde GitHub Actions, el firewall está bloqueando al ejecutor |
| `error:contract` | La API cambió: endpoint 404, respuesta que no es JSON ni UTF-8, campos que faltan | Ver 5.3 |
| `error:validation` | Un valor de la descarga sale del `plausible_range`: ¿evento de mercado real, cambio de escala o de unidad, dato erróneo? | Confirmar con la fuente. La serie no avanza y el estado se repite en cada corrida hasta que la fuente lo corrija o se ajuste el rango; hazlo solo con evidencia (ver las reglas del apartado 6) |
| `error:empty` | La serie no tiene observaciones: `source_series_id` o `source_options` errados, o la fuente no devuelve nada entre `backfill_start` y hoy | Probar el conector con `python -m fxpipe healthcheck` y revisar el id. Si apareció justo después de un arreglo, re-ejecutar una vez antes de investigar (el `error:empty` de Chile de la corrida #20 ya no estaba en la siguiente, sin cambios de código; ver `CHANGELOG.md`) |
| `stale` | La fuente no publica desde hace más de `stale_after_days` | Ver si coincide con feriados del país que publica la fuente (el pipeline no tiene tabla de feriados); si no los explican, ¿la serie se descontinuó? |
| Una cruzada con «insumo degradado» | Uno de sus insumos tiene un `error:*` | Arreglar el insumo; la cruzada se recupera sola en la corrida siguiente |
| El workflow programado no corrió | GitHub desactiva los workflows programados de un repositorio público tras 60 días sin actividad | Actions → seleccionar el workflow → habilitarlo (o `gh workflow enable`) |
| El `git push` del workflow falla | `daily-update.yml` declara `contents: write`; si aun así falla: una regla de protección o ruleset en `main` que rechaza al bot, permisos de Actions limitados por la organización, o un conflicto en `git pull --rebase` | Revisar las reglas de `main` y Settings → Actions → General → Workflow permissions (el README pide **Read and write**); comprobar que el bloque `permissions` del workflow siga intacto |
| El dashboard muestra un dato viejo | Caché del navegador o del CDN | Comparar `as_of` del JSON del repositorio con el que ve el dashboard; `raw.githubusercontent.com` guarda una caché de minutos y jsDelivr una más larga |

## 5. Procedimientos

### 5.1 Re-ejecutar el workflow

Actions → «Actualización diaria de tasas» → Run workflow, sin `backfill_start`. Después, `data/manifest.json` debe
mostrar `overall_status: "ok"`. El paso de cierre cierra solo el issue `fx-alerta` en la primera corrida con todo en
`ok`; no lo cierres a mano.

### 5.2 Backfill

Con `backfill_start` = `AAAA-MM-DD` el workflow recarga el histórico desde esa fecha (`python -m fxpipe backfill
--start AAAA-MM-DD`). Úsalo para corregir valores que un conector había guardado mal o ante una revisión masiva de la
fuente; no hace falta para la carga inicial ni tras una caída (ver el apartado 1). Los valores que cambian quedan
anotados como `revisión`.

Un backfill solo **sobrescribe** las fechas que la fuente devuelve: no borra fechas de más. Si un conector guardó fechas
que no deben existir (por ejemplo, fines de semana o fechas corridas por un `date_basis` mal puesto), esas fechas siguen
en `data/series/<id>.json` después del backfill y hay que retirarlas en un commit explícito y revisado.

### 5.3 Cambio de una API (`error:contract`, issue `api-drift`)

1. Reproducir con `python -m fxpipe healthcheck` y leer la documentación vigente de la fuente (`docs_url` del
   proveedor en `config/sources.json`). Recuerda que algunas fuentes solo se pueden probar de verdad desde GitHub
   Actions (apartado 2).
2. Guardar la respuesta real nueva, sin tokens, en `tests/fixtures/`, junto a la que ya existe.
3. Ajustar **solo el conector** (`src/fxpipe/providers/`); `transform.py` y el contrato no deberían cambiar.
4. Agregar una prueba que detecte el cambio y correr `pytest -q`.
5. Subir `config_version`: la revisión (x.y.**Z**) para corregir un conector, la versión menor (x.**Y**.0) para series
   nuevas o cambios de `primary`.
6. Publicar, ejecutar el workflow con `backfill_start` en la ventana afectada y comprobar el manifest.
7. Cerrar a mano el issue `api-drift`, con el enlace a la corrida en verde.

### 5.4 Secretos

| Secreto | Proveedor | Notas |
|---|---|---|
| `BANXICO_TOKEN` | Banco de México (SIE) | Obligatorio; token gratuito del portal SIE |
| `BCCH_TOKEN` | Banco Central de Chile (BDE) | Obligatorio; **vence al año**; se renueva en Mi Cuenta → Apikey Token (pasos en el [README](../README.md)) |
| `DATOS_GOV_CO_APP_TOKEN` | SFC en datos.gov.co (respaldo de la TRM) | Opcional; mejora los límites de uso |

Los demás proveedores no usan token. Un token vive solo como secreto de GitHub: nunca en el código, en un fixture, en un
commit ni en una URL registrada (el transporte oculta cualquier parámetro con «token» al registrar una URL).

### 5.5 Agregar un país o una serie

Seguir [AGREGAR_PAIS.md](AGREGAR_PAIS.md). El despliegue va en dos fases: primero los datos con `primary: false` (el
dashboard no los muestra) y, cuando el dashboard incorpora los nombres, la bandera y la reserva de alto, `primary:
true` en una versión menor de la configuración. Cada país o serie nueva se cura también en `config/semantic.json`, en
español y en inglés; `python -m fxpipe semantic` lista lo que falta y la CI falla mientras haya huecos. Si el proveedor
necesita un secreto nuevo, además de crearlo hay que exportarlo en el `env:` de `daily-update.yml` y de
`healthcheck.yml`.

### 5.6 Cambiar umbrales

`plausible_range`, `max_daily_change_pct` y `stale_after_days` están por serie en `config/sources.json`. Por
convención, todo cambio de configuración sube `config_version`.

### 5.7 En local

```bash
pip install -e ".[dev]"
export BANXICO_TOKEN=...            # nunca lo subas al repo
export BCCH_TOKEN=...
PYTHONPATH=src python -m fxpipe update --dry-run   # descarga y valida sin escribir en data/
PYTHONPATH=src python -m fxpipe check              # resume data/manifest.json
PYTHONPATH=src python -m fxpipe healthcheck
PYTHONPATH=src python -m fxpipe semantic           # ¿la curaduría cubre toda la configuración?
pytest -q
```

### 5.8 Cambiar el runner o las versiones de las actions

Estado desde 2026-10-18: los tres workflows usan `actions/checkout@v7` y `actions/setup-python@v7` (ambas corren en
Node 24; `@v4` y `@v5` declaraban Node 20, cuyo retiro GitHub fijó para el 2026-09-23). `daily-update.yml` y `healthcheck.yml`
están fijados a `ubuntu-24.04` en vez de `ubuntu-latest` porque GitHub mueve `ubuntu-latest` a Ubuntu 26.04 de forma
gradual entre el 2026-10-19 y el 2026-11-19 y un cambio de imagen no debe romper en silencio una corrida desatendida.
Fuentes: [Ubuntu 26 y migración de `latest`](https://github.blog/changelog/2026-09-17-ubuntu-26-generally-available-and-latest-migration),
[retiro de Node 20](https://github.blog/changelog/2026-03-19-deprecation-of-node-20-on-github-actions-runners/).

Para pasar a Ubuntu 26.04:

1. `ci.yml` ya corre `pytest -q` en `ubuntu-26.04`. Si ese trabajo está en rojo, todavía no hay que mover nada: su
   registro dice qué falló.
2. Con el canario en verde, cambia `runs-on` a `ubuntu-26.04` en `daily-update.yml` y `healthcheck.yml`.
3. Ejecuta `daily-update` a mano y comprueba que el manifest queda en `ok`. Para probar el paso que hace `git push`
   necesitas una corrida en la que cambien los datos; si la corrida manual no cambió nada, espera a la siguiente.

Para subir una acción: abre su `action.yml` en la versión nueva y confirma que `runs.using` es `node24` (o posterior),
cambia la etiqueta mayor en los tres workflows y ejecuta `daily-update` a mano. `ubuntu-24.04` fijado no se mueve
solo, pero tampoco es eterno: revisa este punto cuando GitHub anuncie su retiro.

## 6. Reglas del proyecto

Son convenciones del proyecto; el código solo hace cumplir algunas (la 2, y la curaduría del modelo semántico en la 7).

1. **La fuente es el banco central.** Agregadores y portales abiertos solo entran como verificación o respaldo
   declarado (`fallback`), nunca en silencio.
2. **Solo se cruzan tasas del mismo día de mercado.** Las dos series de una cruzada deben tener el mismo `date_basis`;
   al cargar la configuración, el pipeline lo exige. Ante un país nuevo, se determina con evidencia a qué día de
   mercado corresponde cada fecha publicada antes de cruzar (ver los ejemplos de Chile, Uruguay y Perú en
   [AGREGAR_PAIS.md](AGREGAR_PAIS.md)).
3. **Las cruzadas son cálculos propios**, nunca tasas oficiales, y así se rotulan.
4. **No se relajan los umbrales para que pase un dato** sin evidencia de que ese dato es real.
5. **Todo cambio de conector lleva una respuesta real como fixture y una prueba de contrato.**
6. **El contrato JSON es estable.** Los cambios compatibles suben la versión menor; renombrar, eliminar o cambiar el
   significado de un campo exige versión mayor y publicación en paralelo (ver [CONTRATO_JSON.md](CONTRATO_JSON.md)).
7. **El modelo semántico se versiona.** Si cambia el significado de una serie, un campo o una alerta, sube
   `semantic_version` en `config/semantic.json`, agrega una entrada de `changelog` (es y en) y regenera las tablas con
   `python -m fxpipe semantic --write-docs`.
8. **`data/` lo escribe el pipeline.** `data/series/<id>.json` es el almacén de las observaciones oficiales: lo que
   queda fuera de la ventana de 15 días se conserva tal cual, así que una edición a mano contamina el histórico. Si un
   dato histórico es incorrecto, se corrige el conector y se corre un `backfill` (ver 5.2); las fechas sobrantes, si
   las hay, se retiran en un commit explícito.

## 7. Después de cualquier cambio

1. `pytest -q`, y `python -m fxpipe semantic` si se tocó la configuración.
2. Ejecutar el workflow a mano y comprobar que `data/manifest.json` queda en `overall_status: "ok"`.
3. Confirmar que el dashboard sigue leyendo los archivos (ver [DASHBOARD.md](DASHBOARD.md)).
