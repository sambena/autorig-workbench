# SPDX-License-Identifier: GPL-3.0-or-later
"""Model Studio endpoints; called only after the server's Host/token guard."""
import json
import os
import sys
import uuid
from urllib.parse import quote
from autorig.core import studio


def model(name, server):
    name = server.clean_name(name)
    group = server.find_group(name)
    if group is None: raise ValueError("no such model")
    folder = server.layout.pack_dir(name)
    if not studio.inside(folder, server.layout.ROOT): raise ValueError("model outside collection")
    for path in (os.path.join(folder,"studio.json"), os.path.join(folder,"_studio")):
        if not studio.inside(path,folder): raise ValueError("Studio path outside model folder")
    return name,group,folder


def get(handler, app, server, path, q):
    if path != "/api/studio": return False
    name,group,folder=model((q.get('model') or [''])[0],server)
    settings=studio.load(folder)
    artifacts=studio.artifacts(folder)
    for item in artifacts:
        full=os.path.join(folder,item['path'])
        item['url']='/files/models/'+quote(os.path.relpath(full,server.layout.ROOT).replace('\\','/'))
    handler._send(200,{'model':name,'settings':settings,'tools':studio.TOOLS,'artifacts':artifacts})
    return True


def post(handler,app,server,path,body):
    if path not in ('/api/studio/save','/api/studio/run', '/api/studio/import'): return False
    name,group,folder=model(body.get('model',''),server)
    if path == '/api/studio/import':
        if any(j.model == name and j.state in ('queued', 'running') for j in app.runner.jobs.values()):
            raise ValueError('wait for this model\'s jobs to finish before importing an output')
        from autorig.core.studio_assets import promote_output
        result = promote_output(name, body.get('artifact', ''), body.get('name', ''), body.get('group', ''))
        handler._send(200, result)
        return True
    tool=body.get('tool','');settings=studio.validate(tool,body.get('settings',{}))
    if tool in ('reference','generate') and (settings['folder'] or tool=='generate'):
        studio.input_folder(settings['folder'],folder,[server.layout.ROOT,server.layout.WORK])
    if path.endswith('/save'):
        data=studio.load(folder);data[tool]=settings
        studio.atomic_json(os.path.join(folder,'studio.json'),data)
        handler._send(200,{'saved':True,'settings':settings})
    else:
        snap=os.path.join(server.layout.work_dir('studio_jobs'),uuid.uuid4().hex+'.json')
        studio.atomic_json(snap,settings)
        argv=[sys.executable,'-u',os.path.join(server.STEPS,'studio_job.py'),tool,name,'--settings',snap]
        job=server.Job(name,group,'studio-'+tool,[(tool+': '+name,argv,None,name)])
        handler._send(200,app.runner.submit(job).info())
    return True
