"""OpenTimes' MIT car/CH recipe, adapted to one freshly clipped state job."""
import json
import os
import subprocess
import sys
import time
from common import ROOT, WORK, OUT, STATE, write_json

IMAGE=json.loads((ROOT/'pins/sources.json').read_text())['osrm']['image']
PARENT='plow-pilot.slice'


def docker(command,name):
    return ['docker','run','--rm','--name',name,'--cgroup-parent',PARENT,
            '-v',str(WORK)+':/data',IMAGE,*command]


def run():
    stage=sys.argv[1];sources=json.loads((OUT/'sources.json').read_text())
    def timed(name,args):
        begin=time.monotonic()
        result=subprocess.run(args)
        record={'seconds':time.monotonic()-begin,'returncode':result.returncode}
        write_json(OUT/(name+'.json'),record);print(name+': '+json.dumps(record),flush=True)
        result.check_returncode()
    if stage=='clip':
        source=WORK/'source.osm.pbf';clipped=WORK/(STATE+'.osm.pbf');roads=WORK/'roads.osm.pbf'
        timed('state_clip',['osmium','extract','--strategy=complete_ways','--overwrite','-p',str(WORK/'state.geojson'),str(source),'-o',str(clipped)])
        # Adapted from dfsnow/opentimes data/src/extract_osm.sh (MIT, Dan Snow).
        timed('road_filter',['osmium','tags-filter',str(clipped),'w/highway','w/public_transport=platform','w/railway=platform','w/park_ride','r/type=restriction','--overwrite','-o',str(roads)])
        write_json(OUT/'road-input.json',{'source_bytes':source.stat().st_size,'clipped_bytes':clipped.stat().st_size,'road_pbf_bytes':roads.stat().st_size,'buffer_metres':0})
        print('Road input: '+(OUT/'road-input.json').read_text(),flush=True)
        source.unlink()
    elif stage=='image': timed('image_pull',['docker','pull',IMAGE])
    elif stage=='extract': timed('osrm_extract',docker(['osrm-extract','-p','/opt/car.lua','--threads','4','/data/roads.osm.pbf'],'plow-extract'))
    elif stage=='contract':
        timed('osrm_contract',docker(['osrm-contract','--threads','4','/data/roads.osrm'],'plow-contract'))
        write_json(OUT/'network.json',{'bytes':sum(p.stat().st_size for p in WORK.glob('roads.osrm*')),'algorithm':'CH','profile':'stock /opt/car.lua, traffic-free'})
        print('Network: '+(OUT/'network.json').read_text(),flush=True)
    else: raise ValueError(stage)


if __name__=='__main__':run()
