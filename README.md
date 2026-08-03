# Sincronizador SIGELEC (Oracle 11g / ArcGIS Desktop 10.8.1)

Dos procesos para **sincronizar dos geodatabases** (ORIGEN → DESTINO) de modo que
no queden diferencias: se detectan y aplican **altas (nuevos)**, **bajas
(eliminados)** y **modificaciones**, incluyendo la comparación y transferencia de
**dominios**. Todo pensado para ejecutarse bajo **Python 2.7**.

| | Proceso 1 | Proceso 2 |
|---|---|---|
| **Motor de escritura** | Oracle directo (SQL / `cx_Oracle`) | `arcpy` (ArcGIS Desktop 10.8.1) |
| **Velocidad** | Muy alta | Media (respeta reglas de la GDB) |
| **Red geométrica** | Opcional; no mantiene conectividad | Mantiene la conectividad (sesión de edición) |
| **OBJECTID / GLOBALID** | Se escriben directo (copia GLOBALID → preserva relaciones) | No se pueden escribir en el INSERT → se usan `MIOID`/`MIGUID` + remapeo |
| **Lectura** | Oracle | Atributos desde Oracle (rápido) + geometría con arcpy |
| **Dominios** | Reporta dif. de GDB_ITEMS; sincroniza tabla `DOMINIOS` | Aplica dominios con herramientas arcpy |

---

## 1. Estructura del proyecto

```
sincronizador/
├── comun/                     # Núcleo compartido (Py2/Py3)
│   ├── config.py              # Carga de configuración JSON
│   ├── log.py                 # Logging con Unicode seguro
│   ├── utiles.py              # Normalización, firmas de fila, GUID
│   ├── oracle_db.py           # Envoltura cx_Oracle (binds, metadatos, dominios)
│   ├── modelo.py              # Reglas del modelo (columnas sistema/red/espejo)
│   ├── dominios.py            # Lectura GDB_ITEMS + comparación de dominios
│   ├── comparador.py          # Motor de diferencias (nuevos/modif/elim)
│   └── reporte.py             # Escritor de CSV (UTF-8+BOM) y Excel (openpyxl)
├── proceso1_oracle/
│   └── sincronizar_oracle.py  # PROCESO 1 (entry point)
├── proceso2_arcpy/
│   ├── red_geometrica.py      # Detección/edición/verificación de la red
│   ├── arcpy_io.py            # Cursores + manejo OBJECTID/GLOBALID/MIOID/MIGUID
│   ├── dominios_arcpy.py      # Aplicación de dominios con arcpy
│   └── sincronizar_arcpy.py   # PROCESO 2 (entry point)
├── herramientas/
│   ├── generar_config.py      # Genera config/tablas.json desde el modelo
│   └── reporte_diferencias.py # Reporte CSV/Excel de diferencias (sin aplicar)
├── config/
│   ├── conexiones.json.example
│   ├── tablas.json.example
│   └── tablas.json            # Catálogo completo (160 tablas, 75 relaciones)
├── tests/test_nucleo.py       # Pruebas sin Oracle/arcpy
├── requirements-py27.txt
└── README.md
```

## 2. Instalación

```bash
# Proceso 1 (máquina con cliente Oracle):
pip install "cx_Oracle==7.3.0"        # o la versión compatible con su cliente

# Proceso 2: usar el Python 2.7 que trae ArcGIS Desktop 10.8.1
#   C:\Python27\ArcGIS10.8\python.exe   (arcpy ya viene incluido)
```

Copie la plantilla de conexiones y edítela (no versione credenciales):

```bash
cp config/conexiones.json.example config/conexiones.json
```

`config/tablas.json` ya viene **completo** con las 160 tablas y las relaciones del
modelo (generado desde el diccionario y el archivo de relaciones). Si el modelo
cambia, se puede regenerar:

```bash
python herramientas/generar_config.py \
    --diccionario ruta/diccionario.md \
    --relaciones  ruta/relaciones.md \
    --salida      config/tablas.json
```

El catálogo incluye dos bloques:
- `tablas`: las 160 tablas/feature classes, con `columna_geometria`,
  `red_geometrica` y `llave_negocio` autodetectados.
- `relaciones`: **75** relaciones activas para remapeo:
  - 59 relaciones 1:1 / 1:M (por `GLOBALID` / `CIRCUITSOURCEGUID`).
  - 16 extremos de relaciones **M:N** (por `OBJECTID` o `GUID`). Su
    `tabla_destino` asume la convención de ArcGIS (**tabla intermedia = nombre de
    la relationship class**) y quedan marcados con `_comentario`; ajuste el nombre
    si su esquema difiere.

> Tablas sin `GLOBALID` (cartografía base y tablas intermedias) usan `OBJECTID`
> como llave y quedan marcadas con `_comentario`: revise si deben sincronizarse
> por sí solas o únicamente a través de las relaciones.

## 3. Ejecución

```bash
# PROCESO 1 — Oracle directo
python -m proceso1_oracle.sincronizar_oracle \
    --conexiones config/conexiones.json --tablas config/tablas.json
# opcionales:  --simular   (no escribe, solo reporta)
#              --incluir-red (procesa también las tablas de red geométrica)

# PROCESO 2 — arcpy (con el Python de ArcGIS)
"C:\Python27\ArcGIS10.8\python.exe" -m proceso2_arcpy.sincronizar_arcpy \
    --conexiones config/conexiones.json --tablas config/tablas.json
```

## 4. Reporte de diferencias (vista previa, antes de aplicar)

Antes de escribir nada, genere un **reporte de diferencias** ORIGEN vs DESTINO.
Usa la misma lógica de comparación (vía Oracle, rápido, sin arcpy) y **no aplica
ningún cambio**:

```bash
python herramientas/reporte_diferencias.py \
    --conexiones config/conexiones.json --tablas config/tablas.json \
    --salida reportes/2026-08-03
# opcionales:  --con-geometria   (compara también la geometría, más lento)
#              --incluir-red     (incluye tablas de red geométrica)
```

Genera en la carpeta de salida:

| Archivo | Contenido |
|---|---|
| `resumen.csv` | Una fila por tabla: nuevos, modificados, eliminados, iguales, totales |
| `detalle.csv` | Una fila por diferencia: tipo (NUEVO/MODIFICADO/ELIMINADO), llave, columnas cambiadas |
| `dominios.csv` | Valores de dominio a agregar/quitar/cambiar |
| `reporte_diferencias.xlsx` | Lo mismo en un libro de varias hojas (solo si `openpyxl` está instalado) |

Los CSV se escriben en **UTF-8 con BOM**, así Excel muestra bien tildes y eñes al
abrirlos con doble clic. Revise este reporte y luego ejecute el proceso 1 o 2.

## 5. Conceptos clave del modelo

- **Llave de negocio = `GLOBALID`** (GUID global, estable entre bases). `OBJECTID`
  es local de cada geodatabase.
- **Campos espejo `MIOID` / `MIGUID`**: el proceso 2 guarda ahí el `OBJECTID` y el
  `GLOBALID` del **origen** (porque arcpy asigna unos nuevos al insertar).
- **Columnas de sistema** que nunca se comparan ni copian: `OBJECTID`, `SHAPE`,
  `SHAPE_LENGTH`, `SHAPE_AREA`.
- **Tablas de red geométrica**: detectadas por columnas como `ENABLED`,
  `ELECTRICTRACEWEIGHT`, `FDRMGRNONTRACEABLE`, `PARENTCIRCUITSOURCEGUID`. Son
  **opcionales** (solo se procesan con `incluir_red_geometrica: true`).

## 6. Cómo se preservan las relaciones

### Proceso 1 (Oracle directo)
Como se puede escribir el `GLOBALID`, al insertar en destino se **copia el mismo
`GLOBALID` del origen**. Todas las relaciones que referencian `GLOBALID` quedan
válidas sin remapeo. Solo las relaciones **M:N por `OBJECTID`** requieren remapeo,
que se hace al final con el mapa `OBJECTID_origen → OBJECTID_destino`
(ver bloque `relaciones` con `"tipo_llave": "oid"`).

### Proceso 2 (arcpy)
Al insertar, arcpy asigna **nuevos** `OBJECTID`/`GLOBALID`. El flujo es:

1. Insertar copiando atributos + geometría y guardando
   `MIOID = OBJECTID_origen`, `MIGUID = GLOBALID_origen`.
2. Leer los nuevos `GLOBALID`/`OBJECTID` de destino y construir los mapas
   `guid_origen → guid_destino` y `oid_origen → oid_destino`.
3. **Reparar las relaciones vía Oracle** (rápido): cada columna FK hija que
   apuntaba al valor de origen se actualiza al de destino. Así **no se pierde la
   relación** y se respetan los `GLOBALID` gestionados por ArcGIS (importante para
   la red geométrica).

> Configure cada relación a reparar en el bloque `relaciones` de `tablas.json`
> (nombre, tabla origen/destino, `tipo_llave` `guid`|`oid` y `columna_fk`),
> basándose en el archivo de relaciones del modelo.

## 7. Manejo de caracteres especiales

- `NLS_LANG` y `encoding`/`nencoding` de la conexión se fijan a **UTF-8**.
- Toda lectura se convierte a `unicode` de forma tolerante (`comun.utiles.to_unicode`).
- Toda escritura usa **bind variables** (nunca interpolación de texto), evitando
  problemas con comillas y símbolos.
- El logging codifica a UTF-8 con `errors='replace'` para no abortar por un
  registro con caracteres corruptos.

## 8. Dominios

- **Proceso 1**: lee los dominios reales desde `GDB_ITEMS` en ambas bases y
  **reporta** las diferencias (valores a agregar/quitar/cambiar). La tabla de
  aplicación `DOMINIOS` se sincroniza como una tabla normal. No reescribe el XML
  de `GDB_ITEMS` (riesgoso sin ArcGIS).
- **Proceso 2**: aplica las diferencias con `CreateDomain`,
  `AddCodedValueToDomain`, `DeleteCodedValueFromDomain`, etc. El borrado de un
  dominio completo solo se reporta (puede estar asignado a campos).

## 9. Seguridad y buenas prácticas

- **`--simular`** primero: revise el reporte de diferencias antes de escribir.
- **Transaccional**: el proceso 1 confirma por tabla (rollback ante error); el
  proceso 2 usa una sesión de edición que se aborta completa si algo falla.
- **`politica_borrado`**: `solo_migradas` (por defecto) evita borrar filas nativas
  del destino; use `ninguna` para nunca borrar y `todas` para espejo estricto.
- **Filtros** (`filtro`) para particionar tablas grandes por empresa/provincia.
- Haga **respaldo** del destino antes de la primera corrida.

## 10. Pruebas

```bash
python -m tests.test_nucleo    # valida comparador, firmas, GUID y dominios
```

## 11. Limitaciones conocidas

- El proceso 1 sobre datos **versionados** o de **red geométrica** no mantiene el
  estado SDE ni la conectividad; para esos casos use el proceso 2.
- La comparación/copia de geometría en el proceso 1 asume almacenamiento
  `ST_GEOMETRY` (ajuste `_expr_wkt` para `SDO_GEOMETRY`).
- El proceso 2 asume mismo sistema de coordenadas entre origen y destino; si
  difiere, reproyecte antes.
