# -*- coding: utf-8 -*-
"""
comun.utiles
============

Utilidades transversales de normalizacion y comparacion.  Es el nucleo que
garantiza que dos filas "iguales" en distintas bases produzcan la **misma
firma**, independientemente de:

* Diferencias de codificacion (bytes vs unicode, latin-1 vs utf-8).
* Espacios sobrantes al final de campos ``CHAR`` de Oracle.
* Mayusculas/minusculas y llaves ``{}`` en los GUID.
* Formato de numeros (``1.0`` vs ``1``) y fechas (con/sin milisegundos).

Todas las funciones son puras y compatibles con Python 2.7 / 3.
"""

from __future__ import absolute_import, division, print_function, unicode_literals

import datetime
import hashlib
import numbers

try:
    # En Python 2 el tipo base de texto es `unicode`; en Python 3 es `str`.
    _TEXTO = (str, unicode)  # noqa: F821
except NameError:  # pragma: no cover - Python 3
    _TEXTO = (str,)


def to_unicode(valor, encoding="utf-8"):
    """Convierte cualquier valor a ``unicode`` de forma tolerante.

    Es la funcion clave para el manejo de **caracteres especiales**: nunca
    lanza excepcion; ante bytes invalidos usa ``errors='replace'`` para no
    abortar la sincronizacion por un unico registro corrupto.
    """
    if valor is None:
        return None
    if isinstance(valor, bytes):
        try:
            return valor.decode(encoding)
        except UnicodeDecodeError:
            # Fallback frecuente en datos heredados cargados en latin-1.
            try:
                return valor.decode("latin-1")
            except Exception:
                return valor.decode(encoding, "replace")
    if isinstance(valor, _TEXTO):
        return valor
    # Numeros, fechas, etc.
    return u"%s" % (valor,)


def normalizar_guid(valor):
    """Normaliza un GUID a mayusculas y con llaves ``{...}``.

    ArcGIS almacena los GlobalID/GUID con llaves y en mayusculas
    (``{AB12...}``).  Distintas rutas de lectura pueden entregarlo sin llaves
    o en minusculas; homogeneizamos para que las comparaciones y los mapeos
    de relaciones funcionen.
    """
    if valor is None:
        return None
    texto = to_unicode(valor).strip()
    if not texto:
        return None
    texto = texto.upper()
    nucleo = texto.strip("{}")
    return "{%s}" % nucleo


def _normalizar_valor(valor, decimales=6):
    """Devuelve una representacion canonica y comparable de un valor de celda."""
    if valor is None:
        return u"\x00NULL\x00"

    # Booleanos (algunos drivers devuelven bool para columnas 0/1).
    if isinstance(valor, bool):
        return u"1" if valor else u"0"

    # Numeros: unificar entero/decimal y redondear para absorber ruido de
    # coma flotante entre motores.
    if isinstance(valor, numbers.Number):
        try:
            f = float(valor)
        except (TypeError, ValueError):
            return to_unicode(valor)
        if f == int(f):
            return u"%d" % int(f)
        return (u"%.*f" % (decimales, f)).rstrip("0")

    # Fechas y horas: formato ISO sin zona (Oracle DATE no lleva zona).
    if isinstance(valor, (datetime.datetime, datetime.date)):
        if isinstance(valor, datetime.datetime):
            # Ignorar microsegundos: SDE y Oracle DATE no los conservan igual.
            return valor.replace(microsecond=0).strftime("%Y-%m-%d %H:%M:%S")
        return valor.strftime("%Y-%m-%d")

    # Texto: unicode + recorte de espacios de relleno de CHAR.
    texto = to_unicode(valor)
    return texto.rstrip()


def firma_fila(fila, columnas, decimales=6):
    """Calcula un hash MD5 estable a partir de las columnas indicadas.

    :param fila:      ``dict`` columna->valor (claves en MAYUSCULA).
    :param columnas:  lista ORDENADA de columnas a incluir en la firma.
    :param decimales: precision para redondeo de numeros.
    :return:          cadena hex de 32 caracteres.

    Se usa un separador de control (``\x1f``) improbable en los datos para
    evitar colisiones del tipo ``("a","bc")`` vs ``("ab","c")``.
    """
    partes = []
    for col in columnas:
        partes.append(_normalizar_valor(fila.get(col), decimales))
    cadena = u"\x1f".join(partes)
    return hashlib.md5(cadena.encode("utf-8")).hexdigest()


def diferencias_campos(fila_a, fila_b, columnas, decimales=6):
    """Devuelve la lista de columnas cuyo valor difiere entre dos filas.

    Util para el registro detallado (log) de que cambio en un ``UPDATE``.
    """
    distintas = []
    for col in columnas:
        va = _normalizar_valor(fila_a.get(col), decimales)
        vb = _normalizar_valor(fila_b.get(col), decimales)
        if va != vb:
            distintas.append(col)
    return distintas


def trocear(secuencia, tamano):
    """Divide una secuencia en bloques (lotes) de a lo sumo ``tamano`` elementos.

    Se usa para ejecutar DML/consultas por lotes y no saturar memoria ni el
    limite de expresiones ``IN (...)`` de Oracle (1000 elementos).
    """
    bloque = []
    for elemento in secuencia:
        bloque.append(elemento)
        if len(bloque) >= tamano:
            yield bloque
            bloque = []
    if bloque:
        yield bloque
