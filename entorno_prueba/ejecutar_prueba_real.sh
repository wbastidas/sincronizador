#!/usr/bin/env bash
# ============================================================================
# ejecutar_prueba_real.sh
# Orquesta una prueba end-to-end en un servidor REAL (Oracle + ArcGIS).
# Ajuste las rutas de Python y de config antes de ejecutar.
#
# Requisitos previos:
#   1) sql/01_esquema.sql ejecutado en ORIGEN y DESTINO.
#   2) config/conexiones.json con dsn/usuario/clave (y .sde para el proceso 2).
#   3) cx_Oracle instalado para el Python del proceso 1.
# ============================================================================
set -euo pipefail

# --- Ajuste estos valores -------------------------------------------------
PY_ORACLE="python"                                   # Python 2.7 con cx_Oracle
PY_ARCGIS="/c/Python27/ArcGIS10.8/python.exe"        # Python 2.7 de ArcGIS
CONEX="config/conexiones.json"
TABLAS="entorno_prueba/config_prueba/tablas.json"
N="${1:-20000}"                                      # tamanio (por defecto 20k)
SAL="entorno_prueba/salidas"
# --------------------------------------------------------------------------

echo "== [P1] Generando datos (destino con GLOBALID copiado) =="
$PY_ORACLE entorno_prueba/generar_datos.py --conexiones "$CONEX" --n "$N" --para p1

echo "== [P1] Reporte PREVIO =="
$PY_ORACLE herramientas/reporte_diferencias.py --conexiones "$CONEX" \
    --tablas "$TABLAS" --salida "$SAL/p1_previo"

echo "== [P1] Ejecutando PROCESO 1 (Oracle directo) =="
$PY_ORACLE -m proceso1_oracle.sincronizar_oracle --conexiones "$CONEX" --tablas "$TABLAS"

echo "== [P1] VERIFICACION (debe dar codigo 0 = SINCRONIZADO) =="
$PY_ORACLE herramientas/reporte_diferencias.py --conexiones "$CONEX" \
    --tablas "$TABLAS" --salida "$SAL/p1_verificacion" --verificar
echo "   codigo verificacion P1: $?"

echo "== [P2] Generando datos (destino con identidad en MIGUID) =="
$PY_ORACLE entorno_prueba/generar_datos.py --conexiones "$CONEX" --n "$N" --para p2

echo "== [P2] Ejecutando PROCESO 2 (arcpy) =="
"$PY_ARCGIS" -m proceso2_arcpy.sincronizar_arcpy --conexiones "$CONEX" --tablas "$TABLAS"

echo "== [P2] VERIFICACION por MIGUID (debe dar codigo 0 = SINCRONIZADO) =="
$PY_ORACLE herramientas/reporte_diferencias.py --conexiones "$CONEX" \
    --tablas "$TABLAS" --salida "$SAL/p2_verificacion" --verificar --llave-destino MIGUID
echo "   codigo verificacion P2: $?"

echo "== Prueba real finalizada. Revise los CSV en $SAL =="
