# plow

Free-runner build experiments for fairway drive-time tables and modo browser roads.
Code [MIT](LICENSE); OSM-derived output [ODbL 1.0](https://opendatacommons.org/licenses/odbl/1-0/),
© [OpenStreetMap contributors](https://www.openstreetmap.org/copyright).

Adapts [Dan Snow's MIT OpenTimes recipe](https://github.com/dfsnow/opentimes/tree/e2f91c0b585de3398c3a77e552d64327a0d6bd84):
complete-way clipping, road/restriction filtering, stock OSRM 5.25 car profile and
CH contraction. Dan Snow's notice is retained in LICENSE. Inputs and serializers
are pinned; names, published coordinates, qualifications and missing courses stay intact.

Dispatch **Plow overnight experiments** from main with the exact PR SHA. One free
standard Ubuntu job builds Illinois and Texas with **zero added road buffer**,
clipped to pinned Census TIGER/Line administrative boundaries, not extract extents.
Illinois's fixed 100 origins retain seed `20261029` and the original sampling
extent; all are validated at least 50 km inside its Census border in EPSG:5070.
Failed origins are never replaced.
This sample differs from the earlier unrestricted sample. Clipped-state
OSRM references are fixed across variants; even interior routes can lose border
shortcuts, so this measures approximation on that graph, not full-network truth.

After Illinois's 2 km/10-minute baseline and Texas's 2 km fit/table test, reuse the
Illinois build for 2 km, 1 km and 500 m grids with 10/15/20-minute local searches.
Compare one factor at a time. Test non-service drivable snap candidates at 1 km
and 500 m; routing arcs, course destinations and exact references
stay unchanged. Table points undergo stock OSRM resnapping; coincident-road class
exclusion is not guaranteed. Hybrid searches read the exported modo CSR, including directed
partial edges, and minimize local seconds plus stored cell minutes. Matching
offsets are reported but excluded from time; static costs differ from OSRM CH.

Cells are strictly inside the state. Directed chunks retain raw times ≤5,400 s
before whole-minute rounding. Missing estimates fail accuracy thresholds; nulls,
failed snaps and query errors remain explicit. Goals: ≥90% within two minutes
and >50% nearest-10 orders among eligible origins, using complete payloads only.
The recommendation considers both methods and chooses the smallest tested gzip
payload meeting both goals; a plain lookup needs no local search.

Four cores, 13 GiB cgroup RAM leaving OS headroom, initially no swap. On an actual
OOM, each state gets one retry with 16 GiB disk swap. RAM, process RSS, swap, disk,
exits and per-stage times are measured separately. A 10 GiB free-disk reserve,
processing budget and UTC deadline bound the job. No paid/bigger runner, national
execution, external cache or data commits. Artifact budget defaults to 0; logs
and summary retain results when free shared-storage headroom cannot be verified.
Contracts: `python3 -m unittest discover -s tests -v` (no local road build).
