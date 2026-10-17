# SPDX-License-Identifier: GPL-3.0-or-later
"""Import derived assets and verify the provenance of Studio LOD bundles."""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shlex
import shutil
import struct
import tempfile
from urllib.parse import unquote

import studio

MODEL_EXTS = {'.glb', '.gltf', '.fbx', '.obj'}
IMAGE_EXTS = {'.png', '.jpg', '.jpeg', '.webp', '.tga', '.bmp', '.tif', '.tiff', '.dds', '.exr', '.hdr', '.ktx2', '.basis'}
NAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$')


def digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''): h.update(chunk)
    return h.hexdigest()


def local_file(root, relative):
    if not isinstance(relative, str) or not relative:
        raise ValueError('asset path must be a non-empty relative path')
    parts = relative.replace('\\', '/').split('/')
    if any(p in ('', '.', '..') or ':' in p for p in parts):
        raise ValueError('asset path must stay inside its folder')
    path = Path(root).joinpath(*parts)
    if not studio.inside(path, root) or not path.is_file():
        raise ValueError('asset or dependency missing or outside its folder: ' + relative)
    return path


def dependencies(source):
    """The selected model and its portable local dependencies, never other model variants."""
    source = Path(source)
    if source.suffix.lower() not in MODEL_EXTS: raise ValueError('select a GLB, glTF, FBX or OBJ output')
    root = source.parent
    files = {source}
    def add(uri):
        if uri.startswith('data:'): return None
        path = local_file(root, unquote(uri))
        if path.suffix.lower() not in IMAGE_EXTS | {'.bin', '.mtl'}:
            raise ValueError('unsupported model dependency: ' + uri)
        files.add(path)
        return path
    ext = source.suffix.lower()
    if ext in ('.glb', '.gltf'):
        if ext == '.gltf': data = json.loads(source.read_text(encoding='utf-8'))
        else:
            with source.open('rb') as stream:
                header = stream.read(20)
                if len(header) != 20 or header[:4] != b'glTF' or header[16:20] != b'JSON':
                    raise ValueError('invalid GLB header')
                size = struct.unpack_from('<I', header, 12)[0]
                if size > 64 * 1024 * 1024: raise ValueError('GLB JSON chunk is too large')
                data = json.loads(stream.read(size))
        for record in data.get('buffers', []) + data.get('images', []):
            if 'uri' in record: add(record['uri'])
    elif ext == '.obj':
        for line in source.read_text(encoding='utf-8', errors='replace').splitlines():
            if not line.strip().startswith('mtllib '): continue
            value = line.strip()[7:].strip()
            names = [value] if (root / value).is_file() else shlex.split(value)
            for name in names:
                mtl = add(name)
                for entry in mtl.read_text(encoding='utf-8', errors='replace').splitlines():
                    tokens = entry.strip().split(maxsplit=1)
                    if len(tokens) != 2 or not (tokens[0].lower().startswith('map_') or tokens[0].lower() in ('bump', 'disp', 'decal', 'norm', 'refl')): continue
                    value = tokens[1].strip()
                    texture = value if (mtl.parent / value).is_file() else shlex.split(value)[-1]
                    relative = str(mtl.parent.relative_to(root) / texture).replace('\\', '/')
                    add(relative)
    else:
        # Blender's portable FBX export copies textures beside the FBX or into its .fbm folder.
        files.update(p for p in root.iterdir() if p.suffix.lower() in IMAGE_EXTS and p.is_file())
        fbm = source.with_suffix('.fbm')
        if fbm.is_dir(): files.update(p for p in fbm.rglob('*') if p.is_file() and p.suffix.lower() in IMAGE_EXTS)
    for path in files:
        if not studio.inside(path, root): raise ValueError('dependency outside output folder')
    return sorted(files, key=lambda p: str(p.relative_to(root)))


def promote_output(model, artifact, name, group=''):
    """Copy a result into a new model folder. An existing model is never replaced."""
    import layout
    if not isinstance(name, str) or not NAME.fullmatch(name): raise ValueError('invalid new model name')
    if not isinstance(group, str) or (group and not NAME.fullmatch(group)): raise ValueError('invalid group name')
    if model not in {n for _, n in layout.all_models()}: raise ValueError('unknown source model')
    if name.casefold() in {n.casefold() for _, n in layout.all_models()}: raise ValueError('a model with that name already exists')
    folder = Path(layout.pack_dir(model))
    if not studio.inside(folder, layout.ROOT): raise ValueError('source model outside collection')
    source = local_file(folder, artifact)
    if not studio.inside(source, folder / '_studio'): raise ValueError('select an output under _studio')
    files = dependencies(source)
    parent = Path(layout.ROOT) / group
    if not studio.inside(parent, layout.ROOT): raise ValueError('destination outside collection')
    if parent.exists() and group and layout.is_model_dir(str(parent)): raise ValueError('the group name belongs to a model')
    parent.mkdir(parents=True, exist_ok=True)
    dest = parent / name
    # mkdir is exclusive even with concurrent imports. The model file is published last.
    dest.mkdir()
    complete = False
    try:
        with tempfile.TemporaryDirectory(prefix='_import-', dir=dest) as scratch:
            scratch = Path(scratch)
            for path in files:
                rel = path.relative_to(source.parent)
                target = scratch / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
            origin = {'schema': 'autorig-studio-origin/1', 'model': model, 'artifact': artifact,
                      'sha256': digest(source), 'dependencies': [str(p.relative_to(source.parent)).replace('\\', '/') for p in files]}
            generation = source.parent / 'generation.json'
            if generation.is_file() and studio.inside(generation, folder / '_studio'):
                data = json.loads(generation.read_text(encoding='utf-8'))
                if isinstance(data.get('generation_id'), str): origin['generation_id'] = data['generation_id']
            studio.atomic_json(scratch / 'studio_origin.json', origin)
            for path in sorted(scratch.rglob('*')):
                if not path.is_file() or path == scratch / source.name: continue
                target = dest / path.relative_to(scratch)
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(path, target)
            os.replace(scratch / source.name, dest / source.name)
        complete = True
    finally:
        if not complete and studio.inside(dest, parent): shutil.rmtree(dest)
    return {'name': name, 'group': group, 'source': source.name, 'dir': str(dest), 'origin': origin}


def lod_inputs(model):
    import layout
    folder = Path(layout.pack_dir(model))
    blend = Path(layout.rigged_dir(model)) / (layout.leaf(model) + '.blend')
    geometry = blend if blend.is_file() else Path(layout.source_model(model) or '')
    if not geometry.is_file() or not studio.inside(geometry, folder): raise ValueError('LOD input missing or outside the model')
    paths = [geometry, folder / 'rig.json']
    return [{'path': str(p.relative_to(folder)).replace('\\', '/'), 'sha256': digest(p) if p.is_file() else None} for p in paths]


def finish_lod_report(model, report, out, inputs=None):
    out = Path(out)
    current = lod_inputs(model)
    if inputs is not None and inputs != current: raise ValueError('LOD inputs changed during the run; rerun LOD export')
    report.update(schema='autorig-studio-lods/2', model=model, inputs=current)
    files = {}
    for level in report['levels']:
        path = local_file(out, level['file'])
        for dep in dependencies(path): files[str(dep.relative_to(out)).replace('\\', '/')] = digest(dep)
    report['files'] = files
    studio.atomic_json(out / 'lods.json', report)


def current_lods(model):
    import layout
    root = Path(layout.pack_dir(model)) / '_studio' / 'lods'
    path = root / 'lods.json'
    if not path.is_file(): raise ValueError('run Studio LOD export before packaging LODs')
    report = json.loads(path.read_text(encoding='utf-8'))
    if report.get('schema') != 'autorig-studio-lods/2' or report.get('model') != model:
        raise ValueError('regenerate the LODs to record their provenance')
    if report.get('inputs') != lod_inputs(model): raise ValueError('LODs are out of date: the input or rig spec changed; rerun LOD export')
    levels = report.get('levels', [])
    if not levels or len(levels) > 6 or [l.get('level') for l in levels] != list(range(len(levels))): raise ValueError('invalid LOD level list')
    limit = report.get('error_limit')
    if not isinstance(limit, (int, float)) or not math.isfinite(limit) or not 0 < limit <= .25: raise ValueError('invalid LOD error limit')
    for level in levels:
        local_file(root, level['file'])
        if level['file'] not in report.get('files', {}): raise ValueError('LOD file missing its content hash')
        for mesh in level.get('meshes', []):
            error = mesh.get('surface_error')
            if not isinstance(error, (int, float)) or not math.isfinite(error) or error > limit:
                raise ValueError('LOD failed its surface validation')
    files = report.get('files', {})
    if not isinstance(files, dict) or not files: raise ValueError('LOD bundle has no files')
    for relative, sha in files.items():
        if digest(local_file(root, relative)) != sha: raise ValueError('LOD output changed or is incomplete; rerun LOD export')
    return root, report


def copy_lods(model, dest):
    root, report = current_lods(model)
    dest = Path(dest) / 'lods'
    dest.mkdir(parents=True, exist_ok=True)
    for relative in report['files']:
        source = local_file(root, relative)
        target = dest / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    studio.atomic_json(dest / 'lods.json', report)
    return ['lods/' + p for p in report['files']] + ['lods/lods.json']
