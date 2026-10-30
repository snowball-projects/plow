# plow

Free-runner OSRM car/CH experiments for fairway tables and modo browser roads.
Code [MIT](LICENSE); OSM-derived output [ODbL 1.0](https://opendatacommons.org/licenses/odbl/1-0/),
© [OpenStreetMap contributors](https://www.openstreetmap.org/copyright).
Adapts [Dan Snow's MIT OpenTimes recipe](https://github.com/dfsnow/opentimes/tree/e2f91c0b585de3398c3a77e552d64327a0d6bd84);
his notice is retained in LICENSE. Inputs, OSRM 5.25 and modo serializers are pinned.

Dispatch **Plow overnight experiments** from main with the exact PR SHA.
One standard free Ubuntu job clips Geofabrik extracts with complete ways to
Census TIGER/Line administrative states, with **zero added road buffer**.
The same 100 Illinois origins, seed `20261029`, are at least 50 km inside the
Census border in EPSG:5070; routing failures never replace them. They differ from
the prior unrestricted sample. References stay fixed across each pilot's variants;
clipped-state routes can lose border shortcuts and are not full-network truth.

Illinois tests 2 km, 1 km and 500 m cells with plain and hybrid 10/15/20-minute
searches, then drivable pre-snapping at 1 km and 500 m. Texas gets a 2 km fit/table
test without accuracy. Raw times ≤5,400 s are retained before minute rounding;
directed chunks, missing estimates, nulls and query errors stay explicit.
Hybrid searches use exported modo CSR costs and directed partial edges, minimizing
local seconds plus stored cell minutes. Static costs differ from OSRM CH.
Snap offsets are reported but excluded from time. Pre-snapping excludes service
candidates while retaining routing arcs; stock OSRM resnapping can choose a
coincident road. Signed diagnostic pairs are logged without changing estimates.

Scope: the retained `28e2723` pilot uses 70 Illinois course records (61 points)
and 239 Texas records (154 selected points: 84 missing coordinates and one
coordinate-bearing record excluded for missing destination ID). Future collectors
retain that published coordinate, with its qualification, even without that ID.
Illinois has 325/340 coordinate-bearing mapped facilities and Texas 414/518;
271 and 281 respectively have no course records and are outside this population.
Accuracy, table sizes and national estimates cover the documented-course subset.

Goals: ≥90% within two minutes and >50% identical nearest-10 orders among eligible
origins. Missing estimates fail thresholds. Recommend the smallest complete
payload meeting both goals for either method; plain needs no local search.

Four cores, 13 GiB cgroup RAM with OS headroom, initially no swap. Each state gets
one actual-OOM retry with 16 GiB disk swap. Report RAM/RSS/swap, disk, exits and
stage times separately. A 10 GiB disk reserve, processing budget and UTC deadline
bound work. Artifact budget defaults to 0; logs retain measurements when free
shared-storage headroom cannot be verified. Generated data stays out of Git.
Contracts: `python3 -m unittest discover -s tests -v` (no local road build).
