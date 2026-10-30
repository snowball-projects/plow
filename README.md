# plow

Build experiments for fairway course drive-time tables and modo browser road
files. Code [MIT](LICENSE); OSM-derived output [ODbL 1.0](https://opendatacommons.org/licenses/odbl/1-0/),
© [OpenStreetMap contributors](https://www.openstreetmap.org/copyright).

The car/CH recipe adapts [Dan Snow's OpenTimes](https://github.com/dfsnow/opentimes/tree/e2f91c0b585de3398c3a77e552d64327a0d6bd84)
(MIT notice retained in LICENSE): complete-way clipping, road/restriction
filtering, `osrm-extract -p /opt/car.lua`, `osrm-contract`, and chunked directed
Table API requests. Our network buffer is 300 km; cells and destinations stay
inside the selected state. No DVC, cloud cache, or external routing service.

Run **State OSRM pilot** on the PR branch with its exact implementation SHA. The matrix
contains **Illinois only**; the same job template selects intersecting extracts
for each state. It uses a free standard `ubuntu-24.04` runner, four cores,
a shared 13 GiB cgroup for Python and OSRM containers, no swap, a 10 GiB actual
free-disk reserve, and a five-hour processing stop. Stages and resource peaks
are measured; a capacity stop yields an honest partial report. No national run.

Pinned October 28 Geofabrik inputs and the public October 29 fairway catalog
preserve names, coordinates, qualifications and missing destinations. Grid
centres are 2 km squares inside the state. Cells are sources, courses are
destinations; raw durations ≤5,400 seconds encode as `floor(seconds/60+0.5)`.
Snaps require a drivable match within 1 km; null/error pairs stay explicit,
with no straight-line fallback. Seed `20261029` generates 100 uniform-area
origins (water included). Accuracy compares emitted centre minutes to real-point
OSRM times; missing entries fail thresholds. Nearest-10 compares ordered IDs,
with duration/ID ties, among origins with ten exact courses within 90 minutes.

Outputs stay in Actions: logs/summary and artifacts with one-day retention only
when a verified free shared-storage budget is supplied (default 0 withholds upload).
No releases, data commits, app changes, or paid compute. The report separates
measured values from national planning proxies and does not promise larger
states fit. Browser files use pinned modo serializers and a separate OSM segment
profile; their sizes do not claim OSRM-equivalent turn costs. Local contracts:
`python3 -m unittest discover -s tests -v` (no road build on the laptop).
