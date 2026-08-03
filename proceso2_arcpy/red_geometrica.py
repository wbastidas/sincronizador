# -*- coding: utf-8 -*-
"""
proceso2_arcpy.red_geometrica
=============================

Manejo de las **afectaciones de la red geometrica** (Geometric Network) al
editar con arcpy en ArcGIS Desktop 10.8.1.

Contexto
--------
En la geodatabase SIGELEC existe una red geometrica que conecta las clases
electricas (barras, tramos, puestos, etc.).  Cuando se insertan, modifican o
eliminan features que participan en la red, ArcGIS mantiene la **conectividad
logica** (junctions/edges) y recalcula pesos y direccion de flujo.  Esto tiene
dos implicaciones:

1. **Toda edicion debe hacerse dentro de una sesion de edicion**
   (``arcpy.da.Editor``).  Fuera de una sesion, los cursores de insercion/borrado
   sobre clases de red fallan o corrompen la conectividad.

2. Para **cargas masivas** conviene decidir una estrategia:
   * *En vivo* (por defecto): editar dentro de la sesion; ArcGIS mantiene la red
     en cada operacion.  Es lo mas seguro e integro, aunque mas lento.
   * *Reconstruccion*: si el volumen es muy alto, se puede deshabilitar/borrar la
     red, cargar y luego reconstruirla.  Es mas rapido pero requiere volver a
     crear la red con su definicion; se ofrece como utilidad opcional y NO se usa
     por defecto para no arriesgar la topologia.

Al final del proceso conviene **verificar y reparar** la conectividad.

Todas las llamadas a ``arcpy`` estan encapsuladas aqui para que el resto del
codigo no dependa directamente de la API de geoprocesamiento.
"""

from __future__ import absolute_import, division, print_function, unicode_literals

import os


class RedGeometrica(object):
    """Descubre y gestiona la red geometrica de un workspace SDE."""

    def __init__(self, arcpy, workspace, log):
        self._arcpy = arcpy
        self.workspace = workspace
        self.log = log
        self._redes = None  # rutas a las redes geometricas encontradas.

    # ------------------------------------------------------------------ #
    # Descubrimiento
    # ------------------------------------------------------------------ #
    def localizar_redes(self):
        """Devuelve la lista de rutas a las redes geometricas del workspace.

        Recorre los feature datasets buscando datasets de tipo
        ``GeometricNetwork``.
        """
        if self._redes is not None:
            return self._redes

        arcpy = self._arcpy
        redes = []
        entorno_previo = arcpy.env.workspace
        try:
            arcpy.env.workspace = self.workspace
            for fds in arcpy.ListDatasets("*", "Feature") or []:
                ruta_fds = os.path.join(self.workspace, fds)
                arcpy.env.workspace = ruta_fds
                for ds in arcpy.ListDatasets("*", "GeometricNetwork") or []:
                    redes.append(os.path.join(ruta_fds, ds))
                arcpy.env.workspace = self.workspace
        finally:
            arcpy.env.workspace = entorno_previo

        self._redes = redes
        if redes:
            self.log.info(u"Redes geometricas encontradas: %s",
                          ", ".join(redes))
        else:
            self.log.info(u"No se encontraron redes geometricas en el workspace.")
        return redes

    def clases_en_red(self):
        """Nombres (en mayuscula, sin ruta) de las feature class que participan
        en alguna red geometrica.  Sirve para saber que tablas requieren sesion
        de edicion con manejo especial.
        """
        arcpy = self._arcpy
        nombres = set()
        for red in self.localizar_redes():
            try:
                desc = arcpy.Describe(red)
                for clase in getattr(desc, "featureClassNames", []) or []:
                    nombres.add(clase.split(".")[-1].upper())
            except Exception as exc:  # pragma: no cover
                self.log.warning(u"No se pudo describir la red %s: %s", red, exc)
        return nombres

    # ------------------------------------------------------------------ #
    # Verificacion / reparacion
    # ------------------------------------------------------------------ #
    def verificar_y_reparar(self):
        """Ejecuta ``VerifyAndRepairGeometricNetworkConnectivity`` en cada red.

        Se llama al terminar la carga para asegurar que la conectividad logica
        quedo consistente tras las ediciones.
        """
        arcpy = self._arcpy
        for red in self.localizar_redes():
            try:
                self.log.info(u"Verificando/reparando conectividad de %s ...", red)
                arcpy.VerifyAndRepairGeometricNetworkConnectivity_management(
                    red, "VERIFY_AND_REPAIR")
                self.log.info(u"  Conectividad verificada: %s", red)
            except Exception as exc:  # pragma: no cover
                self.log.error(u"Fallo verificando la red %s: %s", red, exc)

    def rebuild(self):
        """Reconstruye la red (opcional, para cargas masivas).

        No se invoca por defecto.  Util cuando se decidio cargar con la red
        deshabilitada; requiere que la red exista.
        """
        arcpy = self._arcpy
        for red in self.localizar_redes():
            try:
                self.log.info(u"Reconstruyendo red geometrica %s ...", red)
                arcpy.RebuildGeometricNetwork_management(red, "REBUILD_CONNECTIVITY")
            except Exception as exc:  # pragma: no cover
                self.log.error(u"Fallo reconstruyendo la red %s: %s", red, exc)


class SesionEdicion(object):
    """Envoltura de ``arcpy.da.Editor`` como gestor de contexto.

    Abre una sesion (y una operacion) de edicion sobre el workspace.  Es
    **obligatorio** para editar clases que participan en la red geometrica y muy
    recomendable para cualquier edicion en SDE (permite deshacer en bloque).

    Uso::

        with SesionEdicion(arcpy, workspace, log) as ed:
            # ... cursores de insercion/actualizacion/borrado ...
            ed.marcar_ok()   # si no se marca, se hace abort en la salida.
    """

    def __init__(self, arcpy, workspace, log, con_deshacer=True, multiusuario=True):
        self._arcpy = arcpy
        self.workspace = workspace
        self.log = log
        self._con_deshacer = con_deshacer
        self._multiusuario = multiusuario
        self._editor = None
        self._ok = False

    def __enter__(self):
        self._editor = self._arcpy.da.Editor(self.workspace)
        # multiusuario=True es necesario en geodatabases versionadas (SDE).
        self._editor.startEditing(self._con_deshacer, self._multiusuario)
        self._editor.startOperation()
        return self

    def marcar_ok(self):
        self._ok = True

    def __exit__(self, tipo, valor, traza):
        if self._editor is None:
            return
        try:
            if tipo is None and self._ok:
                self._editor.stopOperation()
                self._editor.stopEditing(True)  # guardar cambios.
                self.log.debug(u"Sesion de edicion confirmada.")
            else:
                self._editor.abortOperation()
                self._editor.stopEditing(False)  # descartar cambios.
                if tipo is not None:
                    self.log.error(u"Sesion de edicion abortada por error: %s", valor)
                else:
                    self.log.warning(u"Sesion de edicion abortada (sin marcar_ok).")
        finally:
            self._editor = None
