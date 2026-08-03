# -*- coding: utf-8 -*-
"""
comun.comparador
================

Motor de diferencias **independiente del motor de persistencia**.  Recibe dos
conjuntos de filas (origen y destino) ya indexados por su llave de negocio y
calcula tres colecciones:

* **nuevos**       : llaves presentes en origen y ausentes en destino  -> INSERT
* **eliminados**   : llaves presentes en destino y ausentes en origen  -> DELETE
* **modificados**  : llaves en ambos cuya firma difiere               -> UPDATE

El comparador no sabe si detras hay Oracle o arcpy: solo trabaja con
diccionarios.  Esto permite reutilizar exactamente la misma logica en los dos
procesos y probarla de forma aislada.
"""

from __future__ import absolute_import, division, print_function, unicode_literals

from comun.utiles import firma_fila, diferencias_campos, normalizar_guid


class ResultadoComparacion(object):
    """Contiene el resultado del diff de una tabla."""

    def __init__(self, tabla):
        self.tabla = tabla
        self.nuevos = []        # lista de llaves (str)
        self.eliminados = []    # lista de llaves (str)
        self.modificados = []   # lista de (llave, [columnas_que_cambiaron])
        self.iguales = 0

    def hay_diferencias(self):
        return bool(self.nuevos or self.eliminados or self.modificados)

    def resumen(self):
        return (u"[%s] nuevos=%d modificados=%d eliminados=%d iguales=%d" % (
            self.tabla, len(self.nuevos), len(self.modificados),
            len(self.eliminados), self.iguales))


def _indexar(filas, llave_negocio, es_guid):
    """Construye un dict llave->fila normalizando la llave (GUID si aplica)."""
    indice = {}
    for fila in filas:
        valor = fila.get(llave_negocio.upper())
        if es_guid:
            valor = normalizar_guid(valor)
        else:
            valor = None if valor is None else (u"%s" % valor)
        if valor is None:
            # Fila sin llave de negocio: no se puede sincronizar de forma segura.
            continue
        indice[valor] = fila
    return indice


def comparar(tabla, filas_origen, filas_destino, columnas, llave_negocio,
             firma_geometria=None):
    """Compara dos conjuntos de filas y devuelve un :class:`ResultadoComparacion`.

    :param tabla:          nombre de la tabla (solo para el reporte).
    :param filas_origen:   lista de dicts (columna->valor) del origen.
    :param filas_destino:  lista de dicts del destino.
    :param columnas:       columnas comparables (ver modelo.columnas_comparables).
    :param llave_negocio:  nombre de la columna llave (normalmente GLOBALID).
    :param firma_geometria: dict opcional {llave: hash_geometria} para incluir
                            la geometria en la comparacion sin cargarla en RAM.
    """
    es_guid = llave_negocio.upper() in ("GLOBALID", "GUID", "MIGUID")

    idx_origen = _indexar(filas_origen, llave_negocio, es_guid)
    idx_destino = _indexar(filas_destino, llave_negocio, es_guid)

    resultado = ResultadoComparacion(tabla)

    llaves_origen = set(idx_origen)
    llaves_destino = set(idx_destino)

    # Nuevos y eliminados por diferencia de conjuntos.
    for llave in (llaves_origen - llaves_destino):
        resultado.nuevos.append(llave)
    for llave in (llaves_destino - llaves_origen):
        resultado.eliminados.append(llave)

    # Modificados: comparar firma en la interseccion.
    for llave in (llaves_origen & llaves_destino):
        fo = idx_origen[llave]
        fd = idx_destino[llave]
        firma_o = firma_fila(fo, columnas)
        firma_d = firma_fila(fd, columnas)

        geom_dif = False
        if firma_geometria is not None:
            # firma_geometria mapea llave -> (hash_origen, hash_destino).
            par = firma_geometria.get(llave)
            if par is not None and par[0] != par[1]:
                geom_dif = True

        if firma_o != firma_d or geom_dif:
            cambios = diferencias_campos(fo, fd, columnas)
            if geom_dif:
                cambios.append("SHAPE")
            resultado.modificados.append((llave, cambios))
        else:
            resultado.iguales += 1

    # Orden estable de la salida (facilita el log y las pruebas).
    resultado.nuevos.sort()
    resultado.eliminados.sort()
    resultado.modificados.sort(key=lambda x: x[0])
    return resultado
