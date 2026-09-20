# Instrucciones para agentes — arquitectura

Antes de analizar o modificar COSTURA ÓPTIMA:

1. Lea `docs/architecture/costura-optima.architecture.json` o abra el HTML cuando exista.
2. Identifique los componentes, límites y relaciones afectados.
3. Lea sólo los archivos fuente relevantes indicados por el mapa y su índice de evidencia.
4. Amplíe la búsqueda únicamente si el mapa no resuelve una dependencia.
5. Si el cambio modifica componentes, límites, almacenamiento, flujos runtime, colas, solvers, integraciones o propiedad arquitectónica, actualice y valide el mapa antes de cerrar la tarea.

El mapa es un índice arquitectónico, no una autoridad superior al código: **CODE_WINS**. Si el código lo contradice, corrija el mapa.

Para un cambio arquitectónico, conserve el JSON anterior como baseline y genere un candidate JSON. Ejecute el compare de Archify para producir Before / Delta / After. No es necesario para copy, CSS, tests menores ni refactors internos sin cambio arquitectónico.
