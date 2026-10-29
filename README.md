# plow

OpenStreetMap build experiments for fairway drive-time tables and modo browser
road files. Code [MIT](LICENSE); road-derived output [ODbL 1.0](https://opendatacommons.org/licenses/odbl/1-0/),
© [OpenStreetMap contributors](https://www.openstreetmap.org/copyright).

Run **Illinois pilot** manually with the full implementation commit SHA. Main
has only the dispatch bootstrap; the implementation stays in its draft PR.
The public standard `ubuntu-24.04` runner is free (4 CPU, 16 GB RAM). The pilot
measures its workspace filesystem and preserves 10 GiB free for the system and
packaging. It stops at that reserve, 13 GiB memory, or five hours, retaining
honest partial measurements. It never builds nationwide or modifies apps.

Pinned October 28 Geofabrik extracts cover Illinois, its five land neighbours,
and Michigan. The October 29 published fairway catalog retains names and
coordinates, including property-point qualifications and missing arrivals.
EPSG:5070 cells are 2 km squares. Traffic-free Valhalla runs **origin to course**;
raw durations ≤90 minutes encode as `floor(seconds / 60 + 0.5)`.
100 uniform-area Illinois origins use seed `20261029`; 12 separate synthetic
city/rural/border cases supplement them. Missing centre entries fail accuracy
thresholds; nearest-10 compares ordered IDs, with ID ties, among exact routes
≤90 minutes. Snap failures and routing errors remain explicit.

Outputs are Actions artifacts only, with one-day retention and a caller-supplied
**verified free shared-storage budget** (default 0 withholds upload). Inputs,
merged PBFs and tiles are temporary. No releases or generated-data commits.
The report includes snap/route coverage beside conditional accuracy. National
tile scenarios use input volume; estimates without a measured denominator stay
unsupported. The modo format comes from pinned main;
its separate static OSM segment profile omits turn restrictions and penalties,
so browser sizes do not claim Valhalla-equivalent routing. Run local contracts:
`python3 -m unittest discover -s tests -v` (no road build).
