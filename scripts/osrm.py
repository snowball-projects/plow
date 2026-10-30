"""Small local OSRM client. Directed indices; never request fallback_speed."""
import math
import time
import requests


class QueryError(Exception):
    def __init__(self,code,message=''):
        self.code=code
        super().__init__(f'{code}: {message}')


class Client:
    def __init__(self,session=None):
        self.session=session or requests.Session()

    def request(self,path,params):
        for attempt in range(3):
            try:
                response=self.session.get('http://127.0.0.1:5000/'+path,params=params,timeout=60)
                break
            except (requests.ConnectionError,requests.Timeout):
                if attempt==2:raise
                print(f'OSRM local HTTP transport retry {attempt+1}/2',flush=True)
                time.sleep(.2*(attempt+1))
        data=response.json()
        if data.get('code')!='Ok':raise QueryError(data.get('code','HTTP'+str(response.status_code)),data.get('message',''))
        return data

    def nearest(self,point):
        try:
            data=self.request('nearest/v1/driving/'+','.join(map(str,point)),{'number':1,'radiuses':1000})
            match=data['waypoints'][0]
            if match['distance']>1000:return {'status':'snap_failed'}
            return {'status':'snapped','hint':match['hint'],'distance_metres':match['distance']}
        except QueryError as error:
            return {'status':'snap_failed','osrm_code':error.code}

    def table(self,sources,targets):
        points=sources+targets;n=len(sources)
        # Inspired by OpenTimes' explicit coordinate/source/destination mapping.
        data=self.request('table/v1/driving/'+';'.join(','.join(map(str,p['point'])) for p in points),{
            'sources':';'.join(map(str,range(n))),
            'destinations':';'.join(map(str,range(n,len(points)))),
            'hints':';'.join(p.get('hint','') for p in points),
            'radiuses':';'.join(['1000']*len(points)),
            'annotations':'duration','skip_waypoints':'true'})
        rows=data['durations']
        if len(rows)!=n or any(len(row)!=len(targets) for row in rows):raise ValueError('OSRM matrix dimensions mismatch')
        if any(t is not None and (not math.isfinite(t) or t<0) for row in rows for t in row):raise ValueError('Invalid OSRM duration')
        return rows
