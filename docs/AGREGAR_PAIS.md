# Agregar un país

## Regla 0: la fuente es el banco central

Busca primero el servicio oficial del banco central (o de la autoridad que la
ley designa). Portales de datos abiertos, agregadores o APIs comerciales solo
entran como verificación o respaldo declarado (`fallback` en la config), nunca
como fuente principal si existe la oficial. Documenta en el proveedor
`authority`, `certified_by` y `legal_basis` cuando la cadena institucional no
sea obvia (como la TRM: BanRep la define, la SFC la certifica).

## Caso A: el banco central ya tiene conector (`kind` existente)

Solo editar `config/sources.json`: añadir la serie con su `source_series_id`,
rangos plausibles y umbrales. Curar el modelo semántico (ver abajo). Correr `pytest` y
`python -m fxpipe healthcheck`.

## Caso B: API nueva

1. **Verificar la fuente primaria**: URL oficial de la API, autenticación, formato
   de fechas, unidad (moneda local por USD o al revés), calendario de publicación,
   y si la fecha es de determinación o de vigencia. Guardar una respuesta real
   (sin tokens) como `tests/fixtures/<kind>_ok.json`.
2. Crear `src/fxpipe/providers/<kind>.py` heredando de `Provider`:
   - `fetch()` devuelve `{series_key: {"AAAA-MM-DD": float}}` solo con datos oficiales.
   - Cualquier forma inesperada → `ContractError` con mensaje claro.
   - Si la fuente cotiza USD por moneda local, invertir aquí y documentarlo.
3. Registrar el `kind` en `providers/__init__.py`.
4. Declarar proveedor y serie en `config/sources.json` (secreto en `token_env` si aplica).
5. Si se quiere una cruzada contra COP o MXN, añadir en `derived` usando series con
   el mismo `date_basis`.
6. Pruebas: parseo, detección de cambio de contrato, y caso end-to-end.
7. Crear el secreto en GitHub si la API lo requiere y ejecutar backfill manual.
8. Curar el modelo semántico (ver abajo).
9. Subir la versión menor de `config_version`.

## Curar el modelo semántico (todo país o serie nuevos)

El modelo (`data/semantic_model.json`, [MODELO_SEMANTICO.md](MODELO_SEMANTICO.md)) toma la estructura de
`config/sources.json` y los textos de `config/semantic.json`. Las cruzadas nuevas se describen solas; hay que
curar a mano:

1. En `providers`: protocolo, cuándo publica, condiciones de reutilización y particularidades, con su fuente.
2. En `series`: nombre, descripción, `rate_type` y relaciones de cada serie oficial (p. ej. la variante por día de mercado).
3. En `currencies` y `countries`: el nombre en español e inglés de la moneda y del país.
4. En `known_issues` o `events`, si aplica.

Todo texto va en español **y** en inglés. `python -m fxpipe semantic` lista lo que falta y la CI falla mientras
haya huecos. Después `python -m fxpipe semantic --write-docs` regenera las tablas de MODELO_SEMANTICO.md.

## Ejemplo resuelto: Argentina (config 1.2.0)

- Fuente: API Estadísticas Cambiarias v1.0 del BCRA, sin token.
- Elección de tasa: `REF` (Com. A 3500, mayorista oficial) en lugar de `USD`,
  MEP, CCL o blue, por ser comparable con FIX y TRM.
- Trampas encontradas: el peso mexicano es `MXP` (no `MXN`); fechas futuras
  dan HTTP 400 (se recorta al día de Buenos Aires); rango plausible amplio
  por inflación y devaluaciones (A 3500 ≈ 60 en 2020, ≈ 1.520 en 2026).

## Ejemplo resuelto: Brasil (config 1.4.0)

- Fuente: API PTAX (Olinda, OData) del Banco Central do Brasil, sin token, licencia ODbL.
- Tasa: PTAX de venta de cierre (`cotacaoVenda`), promedio de cuatro consultas
  diarias a dealers según la Resolução BCB nº 45/2020.
- `date_basis: determinacion`: refleja el mercado del mismo día, como FIX y A 3500.
- Trampas: fechas en formato `MM-DD-AAAA` entre comillas simples (`%27`); la hora
  de publicación varía (un registro de ene-2024 salió a las 17:03); el sitio
  bloquea robots, así que la validación en vivo se hace desde GitHub Actions.
- Cruzadas: BRL contra MXN, COP y ARS completan la malla de las cuatro monedas.

## Ejemplo resuelto: Chile (config 1.5.0)

- Fuente: API REST de la Base de Datos Estadísticos (BDE) del Banco Central de Chile,
  serie `F073.TCO.PRE.Z.D` (dólar observado, CLP por USD, dos decimales). Requiere
  cuenta y API Key Token (vence al año): secreto `BCCH_TOKEN`.
- `date_basis: vigencia`, como la TRM: el valor del día D se calcula con el mercado
  del día hábil bancario anterior, se difunde a las 17:30 de ese día y se publica en
  el Diario Oficial el día D. Verificado contra la tabla de la BDE y la del SII.
- Día de mercado (`index: "market_date"`) = fecha de la observación oficial anterior.
  Usa el calendario bancario chileno de la propia serie, sin tabla de feriados.
  Prueba empírica (sep-oct 2026, 26 días): la variación diaria por día de mercado
  correlaciona 0,58 con la PTAX, 0,47 con el FIX y 0,42 con la TRM de mercado; la
  serie por vigencia, sin ajuste, da 0,01, 0,11 y −0,21. Cruzar la vigencia habría
  mezclado días de mercado.
- Trampas: fechas `DD-MM-AAAA` en la respuesta y `AAAA-MM-DD` en la consulta; los
  días sin dato llegan como `"NaN"` con `statusCode: "ND"`; la respuesta viene en
  **ISO-8859-1** (no UTF-8) y el servidor puede comprimirla con gzip; un token inválido
  devuelve HTTP 200 con `Codigo: -5` («Invalid username or password»); el contenedor de
  Claude recibe 403 del proxy, así que la validación en vivo se hace desde GitHub Actions.
- Despliegue gradual: `primary: false` (config 1.5.x) hasta que el dashboard incorporó
  los nombres, la bandera y la reserva de alto; luego `primary: true` (config 1.6.0).

## Ejemplo resuelto: Uruguay (config 1.7.0)

- Fuente: web service SOAP de cotizaciones del Banco Central del Uruguay
  (`awsbcucotizaciones`, especificación v3), sin token, grupo 2 (cotizaciones locales).
- Tasa: código 2225 «DLS. USA BILLETE», el interbancario (promedio de las operaciones del
  día en BEVSA; coincide con lo que informa la prensa, p. ej. 42,572 el 6-sep-2021 y
  38,626 el 25-abr-2024). Desde 2020, cable (2224) y promedio fondo (2230) publican el
  mismo valor. Compra = venta en todo el período: el conector lo exige.
- `date_basis: determinacion`: el cierre del día D se publica el día D. Prueba empírica
  (1.571-1.661 días): la variación diaria correlaciona 0,47 con la PTAX y 0,41 con el FIX
  el mismo día; con ±1 día de desfase, ~0,02.
- Trampas: SOAP por POST con `SOAPAction`; el código 100 («No existe cotización») llega
  en feriados, fines de semana y fechas futuras con un dato vacío (`Moneda` 0): se trata
  como ventana vacía; la especificación limita el rango a 31 días (no se exige en la
  práctica, pero se respeta); las cotizaciones «billete» de otras monedas en el grupo 2
  son del mercado local de billetes y no sirven para controlar las cruzadas.
- Despliegue gradual: `primary: false` (config 1.7.0) y, con el dashboard adaptado,
  `primary: true` (config 1.8.0).

## Ejemplo resuelto: Perú (config 1.9.0)

- Fuente: API de series estadísticas del Banco Central de Reserva del Perú (BCRPData),
  `GET /estadisticas/series/api/{código}/json/{desde}/{hasta}/esp`, sin token. Las condiciones
  de uso permiten reproducir la información citando la fuente.
- Tasa: `PD04638PD` «TC Interbancario (S/ por US$) - Venta». Ojo con el código: `PD04637PD` es
  Compra. El nombre que devuelve la propia API es la comprobación (`expected_name`). Las otras
  series diarias de la página son el sistema bancario SBS (`PD04639PD`/`PD04640PD`), el cierre
  de las 13:30 (`PD04645PD`/`PD04646PD`) y el euro; no se usan.
- Valor: hasta 14 decimales (el BCRP declara 3; en la mayoría de los días son múltiplos de 1/7,
  lo que sugiere un promedio de siete cotizaciones, no documentado). Se guarda sin redondear.
- `date_basis: determinacion`. Prueba empírica (1.693 días): la variación diaria correlaciona
  0,43 con el observado de Chile por día de mercado, 0,38 con el FIX, 0,36 con la TRM por día de
  mercado, 0,35 con la PTAX y 0,26 con el interbancario de Uruguay, el mismo día; con ±1 día de
  desfase, entre -0,03 y 0,07.
- Orientación de las cruzadas: «moneda por 1 PEN», porque el sol vale más que las otras seis y
  así todas las cifras son mayores que 1 (como «COP por 1 UYU» o «UYU por 1 MXN»).
- Trampas: fechas `DD.Mmm.AA` con el mes abreviado (septiembre es «Set» incluso con `ing`);
  los días sin dato llegan como `"n.d."` (feriados peruanos y el día corriente), nunca fines de
  semana; la respuesta es JSON pero se sirve como `text/html`; el sitio está detrás de Imperva
  Incapsula y una consulta mal formada (o, a veces, una válida) devuelve HTTP 200 con una página
  HTML de verificación: se reintenta y, si persiste, es error operativo; el contenedor de Claude
  recibe 403 del proxy, así que la validación en vivo se hace desde GitHub Actions.
- Despliegue gradual: `primary: false` (config 1.9.0) y, con el dashboard adaptado,
  `primary: true` (config 1.10.0).

## Candidatos (NO verificados: confirmar contra la documentación oficial antes de construir)

| País | Institución | Pista de API |
|---|---|---|
| Zona euro | BCE | Data Portal (SDMX), abierta |
