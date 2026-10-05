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

## Candidatos (NO verificados: confirmar contra la documentación oficial antes de construir)

| País | Institución | Pista de API |
|---|---|---|
| Perú | BCRP | API de series estadísticas, abierta |
| Chile | Banco Central de Chile | BDE / SieteRestWS, requiere usuario |
| Zona euro | BCE | Data Portal (SDMX), abierta |
