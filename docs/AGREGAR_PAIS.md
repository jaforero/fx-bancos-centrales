# Agregar un país

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

## Candidatos (NO verificados: confirmar contra la documentación oficial antes de construir)

| País | Institución | Pista de API |
|---|---|---|
| Brasil | Banco Central do Brasil | SGS / PTAX (Olinda), abierta |
| Perú | BCRP | API de series estadísticas, abierta |
| Chile | Banco Central de Chile | BDE / SieteRestWS, requiere usuario |
| Argentina | BCRA | API de estadísticas cambiarias |
| Zona euro | BCE | Data Portal (SDMX), abierta |
