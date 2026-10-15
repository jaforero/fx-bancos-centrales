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
rangos plausibles y umbrales. Correr `pytest` y `python -m fxpipe healthcheck`.

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
8. Subir la versión menor de `config_version`.

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

## Candidatos (NO verificados: confirmar contra la documentación oficial antes de construir)

| País | Institución | Pista de API |
|---|---|---|
| Perú | BCRP | API de series estadísticas, abierta |
| Zona euro | BCE | Data Portal (SDMX), abierta |
