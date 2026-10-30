# plow

Illinois build pilot for fairway drive-time tables and modo browser road files.
Code [MIT](LICENSE); OSM-derived output [ODbL 1.0](https://opendatacommons.org/licenses/odbl/1-0/),
© [OpenStreetMap contributors](https://www.openstreetmap.org/copyright).

Adapts [Dan Snow's MIT OpenTimes recipe](https://github.com/dfsnow/opentimes/tree/e2f91c0b585de3398c3a77e552d64327a0d6bd84):
complete-way clipping and road/restriction filtering, OSRM 5.25 stock car profile,
then CH contraction. Dan Snow's MIT notice is retained in LICENSE. Geofabrik
inputs are pinned to October 28; fairway's public catalog to October 29, 2026.
The catalog has 70 Illinois records, 61 coordinates and 54 distinct destinations;
nine records lack coordinates. Names, coordinates and qualifications stay intact.

Dispatch the manual pilot with the exact PR implementation SHA. The job template
contains Illinois only: a 300 km network buffer, 2 km cell centres inside Illinois,
and Illinois courses as destinations. Directed Table API chunks retain raw times
≤5,400 seconds before rounding to whole minutes. Matches must be within 1 km;
nulls, failed snaps and query errors are explicit, with no straight-line fallback.

Accuracy uses 100 uniform-area random Illinois origins, seed `20261029`, comparing
emitted centre minutes with real-point OSRM seconds. Missing table entries fail
1/2-minute thresholds. Nearest-10 requires identical ordered IDs, sorting by time
then ID, among origins with ten exact courses within 90 minutes.
The hybrid searches the exported modo graph for 600 seconds, minimizing local
seconds plus stored cell minutes over reached centres. Directed partial edges
are included; perpendicular snap offsets are matching distances, excluded from
travel time. Both methods share the same exact references and 100 origins.

Only free standard Ubuntu Actions runners: four cores, shared 13 GiB memory limit,
no swap, 10 GiB free-disk reserve, five-hour processing stop. Logs/summary report
stages, resource peaks, table raw/gzip sizes per 40 km area, browser gzip sizes and
qualified national runner-hours/makespan proxies. Larger states remain unproven.
Browser output uses pinned modo serializers with a separate segment-speed profile
that lacks OSRM turn costs. No national execution, DVC, cache service or data commits.
Artifacts require verified free shared-storage headroom; default budget 0 disables
uploads, leaving measurements in logs/summary. Contracts:
`python3 -m unittest discover -s tests -v` (no local road build).
