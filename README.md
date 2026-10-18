# Tarea 1 — PostGIS de punta a punta: Santiago centro

**Procesamiento Masivo de Datos · ELE051-B / EIN102B · USM 2026-2 · Paralelo 701**
Integrantes: Bastián Maldonado, Matias Gonzalez, Jorge Chacana

Pregunta que ordena el trabajo: **¿cómo se relaciona la ciudad construida (OSM) con la red de
transporte público (GTFS)?** Tres fuentes (OpenStreetMap, GTFS de la DTPM y la DPA 2023) se cargan a
un esquema propio en PostGIS, todo en **SRID 5361**, y se responden 7 preguntas con planes de
ejecución comentados y mapas.

---

## 1. Reproducir desde cero

Requisitos del host: **Docker Engine con el plugin `compose`** y **Python 3** (únicamente librería
estándar para el ETL; `ogr2ogr` y `psql` corren dentro del contenedor). Disco: ~3 GB libres.

```bash
git clone <este repo> && cd tarea1-postgis
# 1) dejar los archivos fuente en ./datos_fuente y ./datos (sección 2)
docker compose up -d --build   # construye la imagen (PostgreSQL 16 + PostGIS 3 + GDAL) y levanta la base
python3 cargar.py              # ETL completo: tablas, reparaciones, índices y VACUUM ANALYZE
python3 ejecutar_consultas.py  # 7 preguntas + planes EXPLAIN ANALYZE + GeoPackage + mapas
```
*(En Windows se puede invocar directamente con `python cargar.py` y `python ejecutar_consultas.py`).*

- `cargar.py` levanta la base por su cuenta (`docker compose up -d --wait`) si no está arriba, espera al
  healthcheck (`pg_isready`) y es **idempotente**: correrlo de nuevo recrea las tablas, repara geometrías,
  reconstruye índices y reinicia los contadores estadísticos.
- Si `datos/osm_santiago-centro.gpkg` aún no existe en la máquina, `cargar.py` invoca automáticamente a
  `preparar_datos.py --zona santiago-centro` usando el `ogr2ogr` del contenedor para recortar el `.osm.pbf`.
- La base queda expuesta en el puerto **5434** del host (`psql -h localhost -p 5434 -U postgres -d postgres`,
  clave `pmd2026`), persistida en el volumen con nombre `pmd-pgdata`.

Tiempo en la máquina de prueba: carga ETL completa ≈ 2 min (**116,9 s**); ejecución de las 7 consultas +
3 planes `EXPLAIN ANALYZE` + exportación de GeoPackage + generación de mapas ≈ **15 s** (el reporte íntegro
queda guardado en `salidas/reporte_consultas_y_planes.txt`).

---

## 2. Datos (no versionados: ver `.gitignore`)

Dejar en `./datos_fuente/` y `./datos/` (`cargar.py` busca automáticamente en ambas carpetas):

| Archivo | De dónde | Descargado el | Versión / fecha de los datos |
|---|---|---|---|
| `chile-261016.osm.pbf` (o `chile-latest.osm.pbf`) | https://download.geofabrik.de/south-america/chile-latest.osm.pbf | 17-10-2026 | **2026-10-16** (Geofabrik, 350 MB) |
| `GTFS_20261003.zip` | https://www.dtpm.cl/index.php/noticias/gtfs-vigente | 17-10-2026 | **03-10-2026** (vigencia 03-10 a 31-12-2026, 12,8 MB) |
| `comunas_cl.sql` | Lab 03 / Lab 05 (`03/datos/comunas_cl.sql`, DPA 2023, INE) | — | **DPA 2023** (218 MB) |

- **Recorte de OSM (`santiago-centro`):** `preparar_datos.py --zona santiago-centro` toma el `.osm.pbf` de
  `datos_fuente/` y genera `datos/osm_santiago-centro.gpkg` (146 MB) recortado a la caja
  `lon [-70.75, -70.52] · lat [-33.52, -33.36]` y reproyectado a **SRID 5361**, verificando exactamente
  **256.060 puntos**, **169.783 líneas** y **146.968 polígonos**. Si `osm_santiago-centro.gpkg` ya está en
  `datos/`, se reutiliza directamente.
- **El GTFS cambió respecto al ejemplo del enunciado:** El enunciado menciona `GTFS_20260829.zip`. Al momento
  de la descarga se encontraba publicado `GTFS_20261003.zip` en el portal de la DTPM. `cargar.py` detecta
  automáticamente cualquier archivo `*GTFS*.zip` en `datos_fuente/` o `datos/` y extrae los `.txt` necesarios
  en `datos/gtfs/`.

---

## 3. Estructura

```text
Dockerfile                              postgres:16-bookworm + postgis 3 + gdal-bin (ogr2ogr / ogrinfo)
docker-compose.yml                      servicio db con healthcheck, puerto 5434 y volumen con nombre pmd-pgdata
preparar_datos.py                       recorte del .osm.pbf a la caja santiago-centro y reproyección a SRID 5361
cargar.py                               ETL reproducible de punta a punta (DPA, OSM, ST_MakeValid, COPY GTFS, índices)
ejecutar_consultas.py                   ejecuta P1..P7, planes EXPLAIN ANALYZE, pg_stat_user_indexes, GPKG y mapas
sql/consultas.sql                       las 6 preguntas obligatorias + P7 propia documentadas en SQL
salidas/resultados_santiago-centro.gpkg capas exportadas con ogr2ogr para QGIS (se versiona, 0,51 MB)
salidas/mapa1_grilla_p6.png             mapa principal P6: edificios OSM vs recorridos GTFS en grilla 500 m (se versiona)
salidas/mapa2_cobertura_y_fuentes.png   mapa secundario P2 y P3: cobertura 500 m y brecha GTFS vs OSM a 50 m (se versiona)
salidas/reporte_consultas_y_planes.txt  salida completa de resultados, planes EXPLAIN ANALYZE y auditoría de índices
datos_fuente/                           (ignorado en Git) archivos crudos descargados (.osm.pbf, GTFS .zip)
datos/                                  (ignorado en Git) comunas_cl.sql, osm_santiago-centro.gpkg y CSVs extraídos
```

---

## 4. Decisiones del pipeline

**SRID 5361 (SIRGAS-Chile 2002 / UTM 19S) en todo.** `preparar_datos.py` reproyecta OSM con
`ogr2ogr -t_srs EPSG:5361` al recortar; las geometrías del GTFS se construyen en `4326` y se transforman con
`ST_Transform(..., 5361)`; `comunas_cl` ya viene en `5361`. En este SRID la unidad es el **metro plano**:
`ST_DWithin(a, b, 500)` evalúa 500 m reales aprovechando directamente los índices planos `GiST`, sin pagar el
costo trigonométrico de proyectar a `geography` en cada fila.

**Carga OSM y `hstore`.** `ogr2ogr` carga las capas `points`, `lines` y `multipolygons` del GeoPackage hacia
`osm_puntos`, `osm_lineas` y `osm_poligonos` con `-lco GEOMETRY_NAME=geom`, `-lco SPATIAL_INDEX=GIST` y
`-lco COLUMN_TYPES=other_tags=hstore` en **9,0 segundos** en total. Convertir `other_tags` a `hstore` permite
extraer etiquetas como `other_tags -> 'amenity'`, `other_tags -> 'ref'` o `other_tags -> 'shelter'` en SQL.

**Reparaciones (`ST_MakeValid`).** Antes de derivar tablas se audita `osm_poligonos` con `NOT ST_IsValid(geom)` y
`ST_IsValidReason(geom)`. Se detectaron **368 polígonos inválidos** sobre 146.968 (0,25 %; área previa declarada
de $8.901.731\text{ m}^2$): **289** por `Self-intersection`, **77** por anillos degenerados de 2 puntos
(`Invalid number of points in LinearRing found 2`) y **2** por `Too few points in geometry component`. Se reparan
con `ST_Multi(ST_CollectionExtract(ST_MakeValid(geom), 3))` para conservar únicamente componentes poligonales
(dimensión 3). Tras la reparación quedan **0 inválidos** y **309 geometrías vacías** (`ST_IsEmpty(geom) = true`,
provenientes de los anillos colapsados sin área), las cuales se excluyen al crear `edificios` y `amenidades`.

**Punto interior vs polígono en `edificios` y `amenidades`.** La tabla `edificios` almacena tanto el polígono
original `geom` (`MultiPolygon`, usado para conservar `area_m2 = ST_Area(geom)`) como su punto interior
garantizado `punto = ST_PointOnSurface(geom)` (`Point`). Para asignar edificios a comunas (P2) y a celdas de la
grilla hexagonal (P6), así como para medir distancias de cobertura a 500 m, se usa `punto`:
1. Evita que un edificio que cruza el borde entre dos hexágonos o comunas se cuente dos veces (la suma de los
   603 hexágonos cuadra exactamente en los **118.973 edificios**).
2. A diferencia de `ST_Centroid`, `ST_PointOnSurface` garantiza matemáticamente que el punto caiga dentro de la
   superficie en edificios con planta en "L", "U" o con patios interiores.
3. En `amenidades` se unen con `UNION ALL` los nodos de `osm_puntos` (`other_tags ? 'amenity'`) y los polígonos
   de `osm_poligonos` (`amenity IS NOT NULL`) representados mediante `ST_PointOnSurface(geom)`, evitando perder
   hospitales, clínicas y colegios mapeados como recintos poligonales.

**GTFS con `COPY` y construcción de geometrías en SQL.** Los 5 archivos CSV (`stops.txt`, `routes.txt`,
`trips.txt`, `shapes.txt` y `stop_times.txt`) se cargan con `COPY` a tablas `UNLOGGED` leyendo dinámicamente los
encabezados del archivo. Los **456.573 puntos** de `shapes.txt` se cargan en **0,5 s** y las **1.098.529
detenciones** de `stop_times.txt` en **1,3 s**. A partir de ellos se construyen:
- `paraderos_gtfs` (**7.162** filas): puntos creados en 4326, transformados a 5361 y recortados **en la base**
  contra el sobre `ST_MakeEnvelope(-70.75, -33.52, -70.52, -33.36, 4326)`.
- `recorridos` (**709** filas): **una `LineString` por `shape_id`** construida con
  `ST_MakeLine(punto ORDER BY shape_pt_sequence::integer)` y filtrada si intersecta la caja en 5361.
- **Manejo de múltiples shapes por ruta:** En el GTFS un mismo recorrido (`route_id` / `route_short_name`) tiene
  varios `shape_id` (ida, vuelta y variantes). Si se hiciera un `JOIN` simple con `trips.txt` (26.159 viajes),
  cada geometría se duplicaría cientos de veces. Por ello se desduplica con `DISTINCT ON (t.shape_id)`
  asociando cada trazado único a su `route_id` y `route_short_name` y guardando `n_viajes`. Así, en P4 se mide
  la longitud de los trazados únicos y en P6 se usa `COUNT(DISTINCT r.route_id)` para contar servicios distintos
  por hexágono sin inflar la oferta por sentidos o variantes.
- `gtfs_frecuencia_paraderos` (**7.147** filas): tabla agregada desde `stop_times.txt` y `trips.txt` con el total
  de detenciones programadas y rutas distintas por cada paradero de la caja, utilizada en la Pregunta 7.

**DPA y `ST_Subdivide`.** Se carga `comunas_cl` (345 comunas) y se genera `comunas_sub` aplicando
`ST_Subdivide(geom, 128)` (131.858 polígonos de a lo sumo 128 vértices) con su respectivo índice GiST para
comparar experimentalmente el recorte de líneas en la Pregunta 4.

**Índices y estadísticas.** Se crean índices `GiST` en todas las columnas geométricas (incluyendo ambos índices
en `edificios`: `idx_edificios_geom` e `idx_edificios_punto`) e índices `B-Tree` en claves de cruce
(`stop_id`, `route_short_name`). El ETL cierra con `VACUUM ANALYZE` y ejecuta `SELECT pg_stat_reset();` para que
`pg_stat_user_indexes` mida exclusivamente el uso de índices durante las consultas P1–P7.

---

## 5. Tablas, conteos e índices

<!-- CONTEOS:INICIO -->
### Fuentes

| Fuente | Archivo | Versión | Fecha de los datos |
|---|---|---|---|
| DPA | `comunas_cl.sql` | División Política Administrativa 2023 (INE/BCN), dump del Lab 03 | 2023 |
| GTFS | `GTFS_20261003.zip` | Red Movilidad DTPM vigente | vigencia 03-10-2026 a 31-12-2026 |
| OSM | `chile-261016.osm.pbf` | Geofabrik chile-latest (350 MB; recorte GPKG 146 MB) | 2026-10-16 |

### Tablas del esquema propio (SRID 5361)

| Tabla / Vista | Filas | Geometría | Índices | Tamaño Datos | Tamaño Índices |
|---|---:|---|---|---:|---:|
| `comunas_cl` | 345 | `ST_MultiPolygon(geom)` | `comunas_cl_geom_idx` (GiST), `comunas_cl_pkey` (B-tree) | 152 kB | 40 kB |
| `comunas_sub` | 131.858 | `ST_Polygon(geom)` | `idx_comunas_sub_geom` (GiST) | 133 MB | 5.280 kB |
| `calles` | 100.921 | `ST_LineString(geom)` | `idx_calles_geom` (GiST) | 20 MB | 4.192 kB |
| `edificios` | 118.973 | `ST_MultiPolygon(geom)`, `ST_Point(punto)` | `idx_edificios_geom` (GiST), `idx_edificios_punto` (GiST) | 30 MB | 9.672 kB |
| `amenidades` | 20.940 | `ST_Point(geom)` | `idx_amenidades_geom` (GiST) | 2.048 kB | 872 kB |
| `paraderos_osm` | 6.572 | `ST_Point(geom)` | `idx_paraderos_osm_geom` (GiST) | 888 kB | 264 kB |
| `paraderos_gtfs` | 7.162 | `ST_Point(geom)` | `idx_paraderos_gtfs_geom` (GiST), `idx_paraderos_gtfs_stop_id` (B-tree) | 768 kB | 464 kB |
| `recorridos` | 709 | `ST_LineString(geom)` | `idx_recorridos_geom` (GiST), `idx_recorridos_route_short` (B-tree) | 2.688 kB | 72 kB |
| `gtfs_frecuencia_paraderos` | 7.147 | — *(atributos agregados)* | `idx_frec_stop_id` (B-tree) | 360 kB | 184 kB |
| `mv_grilla` | 603 | `ST_Polygon(geom)` | `idx_mv_grilla_geom` (GiST) | 88 kB | 40 kB |

### Bitácora del ETL (`cargar.py`)

| Paso | Detalle | Filas / Valor |
|---|---|---:|
| `osm_recorte` | `osm_puntos` importados con `ogr2ogr` (SRID 5361) | 256.060 |
| `osm_recorte` | `osm_lineas` importadas con `ogr2ogr` (SRID 5361) | 169.783 |
| `osm_recorte` | `osm_poligonos` importados con `ogr2ogr` (SRID 5361) | 146.968 |
| `osm_invalidas` | `osm_poligonos` inválidos antes de reparar (`NOT ST_IsValid`) | 368 |
| `osm_invalidas` | área previa declarada de polígonos inválidos ($\text{m}^2$) | 8.901.731 |
| `osm_invalidas` | motivo: `Self-intersection` | 289 |
| `osm_invalidas` | motivo: `Invalid number of points in LinearRing found 2` | 77 |
| `osm_invalidas` | motivo: `Too few points in geometry component` | 2 |
| `osm_reparadas` | `osm_poligonos` inválidos después de `ST_MakeValid` | 0 |
| `osm_descartadas` | `osm_poligonos` vacíos tras extraer polígonos (`ST_IsEmpty = true`) | 309 |
| `osm_tabla` | `calles` (`highway IS NOT NULL`) | 100.921 |
| `osm_tabla` | `edificios` (`building IS NOT NULL AND NOT ST_IsEmpty`) | 118.973 |
| `osm_tabla` | `paraderos_osm` (`highway = 'bus_stop'`) | 6.572 |
| `osm_tabla` | `amenidades` (nodos `other_tags ? 'amenity'` + polígonos con `ST_PointOnSurface`) | 20.940 |
| `gtfs_copy` | `routes.txt` -> `gtfs_routes_raw` (0,2 s) | 428 |
| `gtfs_copy` | `stops.txt` -> `gtfs_stops_raw` (0,2 s) | 18.455 |
| `gtfs_copy` | `trips.txt` -> `gtfs_trips_raw` (0,3 s) | 26.159 |
| `gtfs_copy` | `shapes.txt` -> `gtfs_shapes_raw` (0,5 s) | 456.573 |
| `gtfs_copy` | `stop_times.txt` -> `gtfs_stop_times_raw` (1,3 s) | 1.098.529 |
| `gtfs_tabla` | `paraderos_gtfs` dentro de la caja `santiago-centro` (SRID 5361) | 7.162 |
| `gtfs_tabla` | `recorridos` (`LineString` por `shape_id` que intersecta la caja) | 709 |
| `dpa_tabla` | `comunas_cl` (comunas de Chile en SRID 5361) | 345 |
| `dpa_tabla` | `comunas_sub` (`ST_Subdivide(geom, 128)`) | 131.858 |
| `tiempo` | DPA + `ST_Subdivide` (segundos) | 84,4 |
| `tiempo` | Carga `ogr2ogr` + `ST_MakeValid` + derivadas OSM (segundos) | 12,3 |
| `tiempo` | `COPY` GTFS + geometrías en SQL (segundos) | 5,2 |
| `tiempo` | **TOTAL `cargar.py` (segundos)** | **116,9** |
<!-- CONTEOS:FIN -->

---

## 6. Preguntas (Parte C)

| # | Consulta en `sql/consultas.sql` | Patrón espacial | Plan `EXPLAIN ANALYZE` | Mapa / Salida |
|---|---|---|---|---|
| **P1** | Paraderos por comuna y densidad | Join punto-en-polígono (`ST_Contains`) + `ST_Area(geom)/1e6` | — | `resultados_santiago-centro.gpkg` |
| **P2** | Cobertura de edificios a 500 m | 2 anti-joins `NOT EXISTS` + `ST_DWithin(e.punto, p.geom, 500)` (GTFS vs OSM) | **Sí (Plan #1)** | `mapa2_cobertura_y_fuentes.png` (Panel A) |
| **P3** | Dos fuentes, un objeto (50 m) | Anti-join bidireccional `NOT EXISTS` + `ST_DWithin(..., 50)` | — | `mapa2_cobertura_y_fuentes.png` (Panel B) |
| **P4** | Kilómetros de recorrido por comuna | `ST_Length(ST_Intersection)` líneas × polígonos (`comunas_cl` vs `comunas_sub`) | **Sí (Planes #2A y #2B)** | — |
| **P5** | Vecino más cercano en salud (Ruta `210`) | `CROSS JOIN LATERAL` + operador `<->` + `LIMIT 1` | **Sí (Plan #3)** | — |
| **P6** | Grilla hexagonal de 500 m | `ST_HexagonGrid(500)` + vista materializada `mv_grilla` + Top 10 celdas críticas | — | **`mapa1_grilla_p6.png` (Mapa Principal)** |
| **P7** | Frecuencia real GTFS vs vialidad y refugios OSM | `stop_times` × KNN lateral a `calles` (`<->`) × proximidad a `paraderos_osm` (`shelter='yes'`) | — | Tabla en informe y reporte |

Resultados completos, planes y auditoría de índices: `salidas/reporte_consultas_y_planes.txt`. Capas exportadas: `salidas/resultados_santiago-centro.gpkg`.

**P7: ¿se condice la intensidad operacional de detenciones del GTFS (`stop_times.txt`) con la
jerarquía vial (`calles`) y la disponibilidad de refugios peatonales (`shelter = 'yes'`) en OSM?** Para cada
uno de los 7.162 paraderos GTFS se calcula su volumen de detenciones programadas desde `stop_times.txt`
(1,1 M de filas), se identifica el tipo de calle OSM en que opera mediante un KNN lateral (`<->` a ≤ 40 m contra
`calles`) y se verifica si tiene un paradero OSM con techo (`refugio = 'yes'`) a ≤ 30 m. No es variante de P1–P6:
cruza tres tablas (`gtfs_stop_times_raw`, `calles` y `paraderos_osm`) y revela que mientras los corredores
exclusivos (`busway`, 144,8 detenciones/paradero) tienen 77,4 % de refugios en OSM y las avenidas `primary`
(152,5 detenciones/paradero) tienen 59,9 %, las vías `secondary` y `tertiary` acumulan más de 232.000
detenciones con apenas 35,4 % y 32,2 % de refugios registrados.

---

## 7. Qué no funcionó (y cómo se resolvió)

- **Anillos degenerados de 2 puntos en `osm_poligonos`.** Al auditar los polígonos recortados en el GeoPackage,
  aparecieron 368 geometrías inválidas: además de las 289 `Self-intersection` habituales, había 77 polígonos con
  `Invalid number of points in LinearRing found 2`. Al aplicar `ST_MakeValid`, esos trazados colapsan a líneas o
  puntos; al envolver la reparación con `ST_Multi(ST_CollectionExtract(ST_MakeValid(geom), 3))` quedaron como
  `MULTIPOLYGON EMPTY` (309 filas vacías en total). Se resolvió agregando el filtro explícito
  `AND NOT ST_IsEmpty(geom)` al derivar `edificios` y `amenidades`.
- **`amenity` en nodos vs polígonos de OSM.** En la capa `osm_puntos` de GDAL, la etiqueta `amenity` no viene
  como columna directa sino dentro del `hstore` `other_tags`, mientras que en `osm_poligonos` sí existe como
  columna `amenity`. Además, la mayoría de los hospitales y clínicas grandes están dibujados como polígonos y no
  como puntos. Se resolvió construyendo `amenidades` con un `UNION ALL` entre `osm_puntos`
  (`other_tags ? 'amenity'`) y `osm_poligonos` (`amenity IS NOT NULL`) reducido a punto con `ST_PointOnSurface`.
- **Duplicación masiva de líneas si se cruza `shapes` con `trips` sin agrupar.** En el GTFS hay 26.159 viajes
  (`trips.txt`), pero sólo 981 `shape_id` distintos (709 dentro de la caja). Un `JOIN` directo generaba miles de
  geometrías repetidas para la misma ruta. Se resolvió agrupando primero `shapes.txt` por `shape_id` con
  `ST_MakeLine(... ORDER BY shape_pt_sequence::integer)` (el casteo a `integer` es crítico para evitar orden
  lexicográfico `'10' < '2'`) y asociando cada `shape_id` a su ruta mediante `SELECT DISTINCT ON (t.shape_id)`.
- **`ST_Subdivide` sobre todo Chile en P4.** En la Pregunta 4, `ST_Intersection` contra `comunas_cl` tardó
  649,4 ms y contra `comunas_sub` tardó 621,3 ms (entregando exactamente los mismos kilómetros por comuna, p. ej.
  1.339,99 km en Santiago). El `EXPLAIN ANALYZE` mostró que `ST_Subdivide` redujo el ancho del polígono de
  316.880 bytes a 912 bytes y aceleró 6 veces el `Index Scan` sobre `idx_recorridos_geom` (de 1,94 ms a 0,31 ms
  por loop, bajando los falsos positivos del bbox de 86 a 14). La ganancia total fue moderada porque `comunas_sub`
  subdividió las 345 comunas de Chile en 131.858 piezas, obligando al `Parallel Seq Scan` a descartar 43.729
  subpolígonos de otras regiones por worker antes de cruzar con la RM (`cut_reg = '13'`).
- **Cuello de botella en `matplotlib` al graficar miles de puntos.** Llamar a `ax.scatter()` iterativamente por
  cada punto dentro del bucle de features GeoJSON hacía que el renderizado del Mapa 2 tomara minutos. Se
  vectorizó la función `dibujar_fc` acumulando las coordenadas `(pts_x, pts_y)` en listas y ejecutando una única
  llamada a `ax.scatter()` por capa, reduciendo la generación de ambos mapas a pocos segundos.
- **Índices que no se usaron (`pg_stat_user_indexes`).** Al auditar las lecturas tras correr P1–P7, se detectó que
  `idx_edificios_geom` (4.856 kB) tuvo **`idx_scan = 0`**, mientras que `idx_edificios_punto` (4.816 kB) registró
  **786 lecturas**: como todas las consultas de cobertura y grilla usan el punto interior `edificios.punto`, el
  índice GiST sobre el polígono `geom` resulta redundante y puede eliminarse ahorrando 4,86 MB. Los índices más
  utilizados fueron `idx_paraderos_gtfs_geom` (**370.408** lecturas), `idx_paraderos_osm_geom` (**266.590**
  lecturas) e `idx_calles_geom` (**7.162** lecturas en el KNN de P7).

