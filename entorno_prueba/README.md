# Entorno de prueba

Dos formas de probar el sincronizador:

1. **Demo local (sin Oracle ni ArcGIS)** — ejecuta el código real de los dos
   procesos contra dobles en memoria. Sirve para validar la lógica end‑to‑end en
   cualquier máquina.
2. **Prueba en servidor real (Oracle 11g + ArcGIS Desktop 10.8.1)** — usa las
   tablas de ejemplo, un generador de datos y los `.sde`.

El subconjunto de prueba reproduce una cadena de relaciones representativa:

```
ESTRUCTURAANIVEL ──(GLOBALID→ESTRUCTURANIVELGLOBALID)──► PUNTOCARGA
                                                            └──(GLOBALID→PUNTOCARGAGLOBALID)──► CONEXIONCONSUMIDOR
```

Los datos incluyen **caracteres especiales** (tildes, eñes, comas) y una mezcla
de **nuevos / modificados / eliminados**.

---

## 1. Demo local (recomendada para empezar)

```bash
python -m entorno_prueba.demo_local
```

Ejecuta: reporte previo → Proceso 1 → verificación → Proceso 2 → verificación
(por `MIGUID`). Deja los CSV en `entorno_prueba/salidas/`. Salida esperada:

```
 PROCESO 1: SINCRONIZADO (diferencias restantes=0)
 PROCESO 2: SINCRONIZADO (diferencias restantes=0)
```

## 2. Prueba en servidor real

### Paso 1 — Crear el esquema
Ejecute el DDL en **ambas** bases:

```bash
sqlplus origen/clave@ORIGEN   @entorno_prueba/sql/01_esquema.sql
sqlplus destino/clave@DESTINO @entorno_prueba/sql/01_esquema.sql
```

### Paso 2 — Configurar conexiones
Complete `config/conexiones.json` (dsn/usuario/clave). Para el Proceso 2, cree
los `.sde` con el Python de ArcGIS:

```bat
"C:\Python27\ArcGIS10.8\python.exe" entorno_prueba\crear_conexiones_sde.py ^
    --carpeta C:\conexiones ^
    --host-origen SRVO --servicio-origen ORCL --usuario-origen sde --clave-origen *** ^
    --host-destino SRVD --servicio-destino ORCL --usuario-destino sde --clave-destino *** ^
    --crear-dominio
```

### Paso 3 — Ejecutar la prueba completa
```bash
bash entorno_prueba/ejecutar_prueba_real.sh 20000     # 20.000 estructuras
```

El script hace, para cada proceso: generar datos → reporte previo → sincronizar →
**verificar** (código de salida `0` = SINCRONIZADO). Revise los CSV en
`entorno_prueba/salidas/`.

> El generador crea N estructuras, 2N puntos de carga y 3N conexiones. Con
> `N=20000` son 120.000 filas en origen y una divergencia de decenas de miles de
> altas/bajas/cambios.

## Archivos

| Archivo | Propósito |
|---|---|
| `demo_local.py` | Demo end‑to‑end sin Oracle/ArcGIS (usa `tests/dobles.py`) |
| `sql/01_esquema.sql` | DDL de las tablas de prueba (ejecutar en origen y destino) |
| `generar_datos.py` | Puebla origen y destino divergente vía cx_Oracle |
| `crear_conexiones_sde.py` | Crea los `.sde` y un dominio de prueba (arcpy) |
| `config_prueba/tablas.json` | Configuración de las 3 tablas y 2 relaciones |
| `ejecutar_prueba_real.sh` | Orquesta generar → reportar → sincronizar → verificar |

## Nota sobre geometría y red geométrica

El DDL crea tablas de atributos (suficientes para probar el Proceso 1 y las
relaciones). La geometría `ST_GEOMETRY` y la red geométrica requieren feature
classes registradas en la geodatabase; esa parte debe validarse sobre su
geodatabase real (el Proceso 2 ya la maneja dentro de una sesión de edición).
