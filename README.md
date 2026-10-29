# plow

OpenStreetMap build experiments for fairway course drive-time tables and modo
browser road files. The Illinois pilot runs manually on a free public Ubuntu
Actions runner. It stops at its resource budget and reports measured partial
results. Nothing runs nationwide.

The implementation is reviewed in a draft PR. Main contains only this dispatch
entrypoint so Actions can run an immutable PR commit without merging it. All
pilot outputs belong in workflow artifacts; no releases or generated-data
commits. Artifact upload requires verified free shared-storage headroom.

Code: [MIT](LICENSE). Road-derived output: [ODbL 1.0](https://opendatacommons.org/licenses/odbl/1-0/),
© [OpenStreetMap contributors](https://www.openstreetmap.org/copyright).
