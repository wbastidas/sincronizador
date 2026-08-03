# -*- coding: utf-8 -*-
"""
proceso2_arcpy.arcpy_io
=======================

Capa de entrada/salida con **arcpy** (ArcGIS Desktop 10.8.1, Python 2.7).

Aqui vive la parte delicada que describio el requerimiento:

* Al **insertar** con arcpy **no se puede escribir OBJECTID ni GLOBALID**
  (ArcGIS los asigna).  Por eso, en cada insercion:
    - se copian todos los atributos de negocio y la geometria,
    - se guarda el **OBJECTID de origen en el campo ``MIOID``**,
    - se guarda el **GLOBALID de origen en el campo ``MIGUID``**.
* Tras insertar, ``insertRow`` devuelve el **nuevo OBJECTID**.  Con el se lee el
  **nuevo GLOBALID** asignado por ArcGIS.  Asi se construyen los mapas:
    - ``guid_origen -> guid_destino``
    - ``oid_origen  -> oid_destino``
  que despues usa Oracle para **reparar las relaciones** (evitar que se pierda
  la relacion, tal como pide el requerimiento).
* Las **actualizaciones** se localizan por ``MIGUID`` (el GLOBALID de origen que
  guardamos), no por el GLOBALID de destino (que es distinto).
* **Caracteres especiales:** todos los valores se pasan como ``unicode``; arcpy
  en 10.8.1 los maneja correctamente si el campo es de texto y el cliente esta
  en UTF-8.

Los cursores usan siempre el token ``SHAPE@WKB`` para mover la geometria como
binario WKB, que es portable y evita depender del sistema de coordenadas exacto
de origen (se asume mismo SRID; si no, se reproyecta antes).
"""

from __future__ import absolute_import, division, print_function, unicode_literals

from comun.utiles import normalizar_guid, to_unicode


class EscritorArcpy(object):
    """Aplica INSERT/UPDATE/DELETE sobre una feature class/tabla via arcpy.da."""

    def __init__(self, arcpy, log):
        self._arcpy = arcpy
        self.log = log

    # ------------------------------------------------------------------ #
    # Lectura de existentes en destino (para el diff)
    # ------------------------------------------------------------------ #
    def leer_destino_indexado(self, ruta, columnas, tiene_geometria):
        """Lee el destino y lo indexa por ``MIGUID`` (GLOBALID de origen).

        Devuelve (indice, tokens) donde:
          * indice: {miguid_normalizado: dict fila con OBJECTID, GLOBALID, MIOID,
                     columnas...}
          * las filas con MIGUID nulo (nativas del destino, no migradas) se
            devuelven aparte para respetar la politica de borrado.
        """
        arcpy = self._arcpy
        campos = list(columnas)
        for extra in ("OID@", "GLOBALID", "MIGUID", "MIOID"):
            if extra not in campos:
                campos.append(extra)
        if tiene_geometria:
            campos.append("SHAPE@WKB")

        indice = {}
        nativas = []
        with arcpy.da.SearchCursor(ruta, campos) as cur:
            for registro in cur:
                fila = dict(zip(campos, registro))
                miguid = normalizar_guid(fila.get("MIGUID"))
                fila_norm = self._normalizar_fila(fila)
                if miguid:
                    indice[miguid] = fila_norm
                else:
                    nativas.append(fila_norm)
        return indice, nativas

    # ------------------------------------------------------------------ #
    # INSERT
    # ------------------------------------------------------------------ #
    def insertar(self, ruta, filas_origen, columnas, tiene_geometria,
                 geometria_por_llave, llave_negocio):
        """Inserta filas nuevas y devuelve los mapas de identidad.

        :param filas_origen:        lista de dicts (datos completos del origen).
        :param columnas:            columnas de negocio a escribir (sin OBJECTID
                                    ni GLOBALID ni SHAPE).
        :param geometria_por_llave: {llave: wkb} con la geometria del origen.
        :return: dict {
                    'guid': {guid_origen: guid_destino},
                    'oid':  {oid_origen: oid_destino},
                 }
        """
        arcpy = self._arcpy
        llave = llave_negocio.upper()

        # Campos del cursor: negocio + MIOID + MIGUID (+ geometria).
        campos = list(columnas)
        for espejo in ("MIOID", "MIGUID"):
            if espejo not in campos:
                campos.append(espejo)
        if tiene_geometria:
            campos.append("SHAPE@WKB")

        mapa_guid = {}
        mapa_oid = {}
        nuevos_oids = []  # (guid_origen, nuevo_oid) para leer luego el GLOBALID.

        with arcpy.da.InsertCursor(ruta, campos) as cur:
            for fila in filas_origen:
                valores = []
                for c in columnas:
                    valores.append(self._valor_salida(fila.get(c)))
                # Campos espejo: guardan la identidad del origen.
                oid_origen = fila.get("OBJECTID")
                guid_origen = normalizar_guid(fila.get(llave))
                valores.append(oid_origen)                 # MIOID
                valores.append(guid_origen)                # MIGUID
                if tiene_geometria:
                    valores.append(geometria_por_llave.get(guid_origen))
                nuevo_oid = cur.insertRow(valores)
                mapa_oid[oid_origen] = nuevo_oid
                nuevos_oids.append((guid_origen, nuevo_oid))

        # Leer los GLOBALID reci asignados por ArcGIS (mapa guid_origen->destino).
        mapa_guid = self._leer_guids_nuevos(ruta, nuevos_oids)
        self.log.info(u"  INSERT %d filas (arcpy) en %s",
                      len(mapa_oid), ruta.split("\\")[-1].split("/")[-1])
        return {"guid": mapa_guid, "oid": mapa_oid}

    def _leer_guids_nuevos(self, ruta, pares_guid_oid):
        """Dado [(guid_origen, nuevo_oid)], lee el GLOBALID de destino por OID."""
        arcpy = self._arcpy
        por_oid = {}
        oid_a_guid_origen = {}
        for guid_origen, nuevo_oid in pares_guid_oid:
            oid_a_guid_origen[nuevo_oid] = guid_origen

        # Leer en bloque los GLOBALID de destino filtrando por MIGUID no nulo es
        # mas simple: recorremos por OID@ y GLOBALID.
        campos = ["OID@", "GLOBALID"]
        mapa = {}
        with arcpy.da.SearchCursor(ruta, campos) as cur:
            for oid, gid in cur:
                if oid in oid_a_guid_origen:
                    mapa[oid_a_guid_origen[oid]] = normalizar_guid(gid)
        return mapa

    # ------------------------------------------------------------------ #
    # UPDATE
    # ------------------------------------------------------------------ #
    def actualizar(self, ruta, cambios_por_miguid, columnas, tiene_geometria,
                   geometria_por_llave):
        """Actualiza filas existentes localizadas por ``MIGUID``.

        :param cambios_por_miguid: {miguid: (fila_origen, [columnas_cambiadas])}.
        """
        arcpy = self._arcpy
        campos = list(columnas)
        if "MIGUID" not in campos:
            campos.append("MIGUID")
        idx_miguid = campos.index("MIGUID")
        campo_wkb = None
        if tiene_geometria:
            campos.append("SHAPE@WKB")
            campo_wkb = len(campos) - 1

        actualizados = 0
        with arcpy.da.UpdateCursor(ruta, campos) as cur:
            for registro in cur:
                miguid = normalizar_guid(registro[idx_miguid])
                if miguid not in cambios_por_miguid:
                    continue
                fila_origen, cambiadas = cambios_por_miguid[miguid]
                fila_lista = list(registro)
                for i, c in enumerate(columnas):
                    fila_lista[i] = self._valor_salida(fila_origen.get(c))
                if tiene_geometria and "SHAPE" in [x.upper() for x in cambiadas]:
                    fila_lista[campo_wkb] = geometria_por_llave.get(miguid)
                cur.updateRow(fila_lista)
                actualizados += 1
        self.log.info(u"  UPDATE %d filas (arcpy)", actualizados)
        return actualizados

    # ------------------------------------------------------------------ #
    # DELETE
    # ------------------------------------------------------------------ #
    def eliminar(self, ruta, miguids_a_borrar):
        """Elimina filas cuyo ``MIGUID`` este en el conjunto dado."""
        arcpy = self._arcpy
        objetivo = set(miguids_a_borrar)
        if not objetivo:
            return 0
        borrados = 0
        with arcpy.da.UpdateCursor(ruta, ["MIGUID"]) as cur:
            for registro in cur:
                if normalizar_guid(registro[0]) in objetivo:
                    cur.deleteRow()
                    borrados += 1
        self.log.info(u"  DELETE %d filas (arcpy)", borrados)
        return borrados

    # ------------------------------------------------------------------ #
    # Auxiliares
    # ------------------------------------------------------------------ #
    def _valor_salida(self, valor):
        """Prepara un valor para escritura via arcpy (texto a unicode)."""
        if isinstance(valor, bytes):
            return to_unicode(valor)
        return valor

    def _normalizar_fila(self, fila):
        salida = {}
        for k, v in fila.items():
            salida[k.upper() if isinstance(k, (str, bytes)) else k] = \
                to_unicode(v) if isinstance(v, bytes) else v
        # Homogeneizar la clave del OID.
        if "OID@" in fila:
            salida["OBJECTID"] = fila["OID@"]
        return salida


def leer_geometria_origen(arcpy, ruta, llave_negocio):
    """Lee la geometria del origen como WKB, indexada por la llave de negocio.

    Se lee con arcpy (no con Oracle) porque es la via mas fiable para la
    geometria SDE; los atributos, en cambio, se leen desde Oracle por velocidad.

    :return: {guid_normalizado: wkb}
    """
    llave = llave_negocio.upper()
    geometria = {}
    with arcpy.da.SearchCursor(ruta, [llave, "SHAPE@WKB"]) as cur:
        for k, wkb in cur:
            geometria[normalizar_guid(k)] = wkb
    return geometria
