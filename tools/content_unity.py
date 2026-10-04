"""Pinned UnityPy adapter. Raw records survive unsupported conversion features.

Real bundle decoding is a separate test gate from the synthetic adapter tests.
A catalog never implies that every object's content can be rendered by Godot.
"""
from __future__ import annotations
import base64
import importlib.metadata
import json
import os
import shutil
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Callable
try:
    from .content_pipeline import (AssetIndex, ContentError, SCHEMA, UNITY_VERSION, UNITYPY_VERSION,
        canonical, encode_tree, decode_tree, object_id, safe_child, verify_source, verify_catalog)
except ImportError:
    from content_pipeline import (AssetIndex, ContentError, SCHEMA, UNITY_VERSION, UNITYPY_VERSION,
        canonical, encode_tree, decode_tree, object_id, safe_child, verify_source, verify_catalog)


def load_unitypy():
    try:
        import UnityPy
        import UnityPy.config
    except ImportError as e:
        raise ContentError('UnityPy is missing; install tools/content-requirements.txt in an isolated venv') from e
    if UnityPy.__version__!=UNITYPY_VERSION:
        raise ContentError(f'Expected UnityPy {UNITYPY_VERSION}; found {UnityPy.__version__}')
    UnityPy.config.FALLBACK_UNITY_VERSION=UNITY_VERSION
    return UnityPy


def source_files(root: Path) -> list[dict]:
    if not root.is_dir() or root.is_symlink():
        raise ContentError('Source must be an extracted game-data directory, not a DMG or an LFS pointer')
    found=[]
    for path in sorted(root.rglob('*')):
        if path.is_symlink():raise ContentError('Input symlink is not followed: '+str(path))
        if not path.is_file():continue
        rel=path.relative_to(root).as_posix()
        if any(':com.apple.' in part or part.startswith('._') or part=='.DS_Store' for part in path.relative_to(root).parts):continue
        # Preserve hashes for banks, catalogs and resource streams too. No exact
        # bundle count is assumed from contradictory historical documentation.
        if path.stat().st_size==0:raise ContentError('Empty input file: '+rel)
        found.append({'path':rel,**verify_source(path),
                      'kind':'bundle' if path.suffix=='.bundle' else 'bank' if path.suffix=='.bank' else 'auxiliary'})
    if not any(f['kind']=='bundle' for f in found):
        raise ContentError('No real .bundle files found; JSON definitions alone are not media')
    return found


def _parse(obj):
    return obj.parse_as_dict()


def _artifact(out: Path, name: str, payload: bytes, role: str) -> dict:
    path=safe_child(out,name);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('xb') as f:f.write(payload)
    return {'role':role,'path':name,**verify_source(path)}


def decode_mesh(obj) -> dict:
    from UnityPy.helpers.MeshHelper import MeshHandler
    data=obj.parse_as_object();handler=MeshHandler(data);handler.process()
    result={'positions':handler.m_Vertices,'submeshes':[[i for tri in part for i in tri] for part in handler.get_triangles()]}
    for dst,src in [('normals','m_Normals'),('tangents','m_Tangents'),('colors','m_Colors'),('joints','m_BoneIndices'),('weights','m_BoneWeights')]+[(f'uv{i}',f'm_UV{i}') for i in range(8)]:
        val=getattr(handler,src,None)
        if val:
            result[dst]=[list(row[:3]) if dst=='normals' else list(row) for row in val]
    if not result['positions'] or not result['submeshes'] or any(not part for part in result['submeshes']):
        raise ContentError('Mesh decoder returned empty vertices or primitives')
    return result


def inventory(root: Path, output: Path, *, loader: Callable | None=None,
              mesh_decoder: Callable | None=None) -> dict:
    """Read source without modifying it; create a fresh diagnostics/catalog tree.

    Injected loaders are for tests and label the output synthetic. CLI always
    calls the real pinned UnityPy loader. Errors are recorded and gate export.
    """
    sources=source_files(root)
    if output.exists() or output.is_symlink() or output.resolve().is_relative_to(root.resolve()):
        raise ContentError('Use a new output directory outside the source tree')
    injected=loader is not None
    if loader is None:loader=load_unitypy().load
    decoder=mesh_decoder or decode_mesh
    output.parent.mkdir(parents=True,exist_ok=True)
    stage=Path(tempfile.mkdtemp(prefix='content-',dir=output.parent))
    files=[];objects=[];errors=[]
    try:
        for source in sources:
            if source['kind']!='bundle':continue
            path=safe_child(root,source['path'])
            try:
                env=loader(str(path));readers=list(env.objects)
                if not readers:raise ContentError('Bundle has no decoded serialized objects')
            except Exception as e:
                errors.append({'source':source['path'],'reason':type(e).__name__+': '+str(e)})
                continue
            owning={}
            for obj in readers:
                sf=obj.assets_file
                if sf.name in owning and owning[sf.name] is not sf:
                    raise ContentError('Duplicate serialized-file names within bundle '+source['path'])
                owning[sf.name]=sf
            for name,sf in sorted(owning.items()):
                key=source['path']+'/'+name
                files.append({'key':key,'bundle':source['path'],'name':name,
                              'externals':[e.path for e in sf.externals],
                              'external_metadata':[{'path':e.path,'guid':encode_tree(getattr(e,'guid',None)),
                                                    'type':getattr(e,'type',None)} for e in sf.externals]})
            for obj in sorted(readers,key=lambda o:(o.assets_file.name,o.path_id)):
                fk=source['path']+'/'+obj.assets_file.name;identity=object_id(fk,obj.path_id)
                record={'id':identity,'file':fk,'path_id':str(obj.path_id),'type':obj.type.name,'name':'',
                        'artifacts':[],'references':[],'errors':[],'conversion':'raw-only'}
                try:
                    raw=obj.get_raw_data()
                    record['artifacts'].append(_artifact(stage,f'objects/{identity}.bin',raw,'raw-object'))
                    tree=_parse(obj)
                    record['name']=tree.get('m_Name','') if isinstance(tree.get('m_Name',''),str) else ''
                    record['artifacts'].append(_artifact(stage,f'objects/{identity}.json',canonical(encode_tree(tree)),'typetree'))
                except Exception as e:
                    record['errors'].append({'field':'','reason':'decode: '+type(e).__name__+': '+str(e)})
                    objects.append(record);continue
                if obj.type.name=='Mesh':
                    try:
                        mesh=decoder(obj)
                        record['artifacts'].append(_artifact(stage,f'meshes/{identity}.json',canonical(encode_tree(mesh)),'decoded-mesh'))
                        record['conversion']='decoded-mesh-not-assembled'
                    except Exception as e:record['errors'].append({'field':'','reason':'mesh: '+str(e)})
                elif obj.type.name in ('Texture2D','Sprite'):
                    try:
                        import io
                        image=obj.parse_as_object().image
                        if image is None or min(image.size)<1:raise ContentError('Empty decoded image')
                        buf=io.BytesIO();image.convert('RGBA').save(buf,format='PNG',compress_level=9)
                        record['artifacts'].append(_artifact(stage,f'images/{identity}.png',buf.getvalue(),'decoded-image'))
                        record['conversion']='decoded-image-not-bound'
                    except Exception as e:record['errors'].append({'field':'','reason':'image: '+str(e)})
                # Skins, bind poses, Animator state, clips, renderers, material
                # properties, colliders, LOD and VFX typetrees remain intact.
                objects.append(record)
            del readers,env
        idx=AssetIndex(files,objects)
        for identity,record in sorted(idx.objects.items()):
            typed=next((a for a in record['artifacts'] if a['role']=='typetree'),None)
            if typed:
                tree=decode_tree(json.loads(safe_child(stage,typed['path']).read_text()))
                refs,missing=idx.references(identity,tree)
                record['references']=refs;record['errors'].extend(missing)
        object_errors=sum(len(o['errors']) for o in idx.objects.values())
        catalog={'schema':SCHEMA,'status':'incomplete' if errors or object_errors else 'indexed',
                 'source_status':'synthetic-adapter-fixture' if injected else 'unity-bundle-bytes-read',
                 'unity_version':UNITY_VERSION,'unitypy_version':None if injected else UNITYPY_VERSION,
                 'sources':sources,'files':sorted(files,key=lambda f:f['key']),
                 'objects':sorted(idx.objects.values(),key=lambda o:o['id']),'errors':errors,
                 'counts':{'bundles':sum(s['kind']=='bundle' for s in sources),'objects':len(idx.objects),
                           'types':dict(sorted(Counter(o['type'] for o in idx.objects.values()).items())),
                           'errors':len(errors)+object_errors},
                 'original_media_complete':False,'godot_imported':False,
                 'limitations':['Addressables GUIDs need catalog evidence, not filename matching.',
                                'Raw Animator/clip/LOD/VFX records do not imply converted runtime behavior.',
                                'Banks are hashed, not decoded as audio. Fonts/raw media are local-only.']}
        # Catch mutations/races in source reads before publishing any catalog.
        for source in sources:
            verify_source(safe_child(root,source['path']),expected_sha256=source['sha256'],expected_size=source['bytes'])
        (stage/'catalog.json').write_bytes(canonical(catalog))
        verify_catalog(stage)
        if output.exists():raise ContentError('Output appeared during inventory')
        os.rename(stage,output)
        return catalog
    except Exception:
        shutil.rmtree(stage,ignore_errors=True)
        raise


class SceneReader:
    """Assemble explicit static/skinned renderer hierarchies from an inventory.

    No Animator/humanoid, morph, LOD, lightmap or custom-shader behavior is guessed.
    Unsupported features block the renderer export while raw recovery is retained.
    """
    def __init__(self,directory: Path):
        self.directory=directory;self.catalog=verify_catalog(directory)
        self.idx=AssetIndex(self.catalog['files'],self.catalog['objects']);self.cache={}
        self.unsupported=[]

    def tree(self,key: str) -> dict:
        if key not in self.cache:
            record=self.idx.objects[key]
            if record['errors']:raise ContentError('Source asset has errors: '+key+' '+str(record['errors']))
            a=next((a for a in record['artifacts'] if a['role']=='typetree'),None)
            if a is None:raise ContentError('Missing typetree: '+key)
            self.cache[key]=decode_tree(json.loads(safe_child(self.directory,a['path']).read_text()))
        return self.cache[key]

    def ptr(self,key: str,value: dict) -> str | None:
        return self.idx.resolve(self.idx.objects[key]['file'],value)

    def artifact(self,key: str,role: str) -> bytes:
        a=next((a for a in self.idx.objects[key]['artifacts'] if a['role']==role),None)
        if a is None:raise ContentError('Missing '+role+' on '+key)
        return safe_child(self.directory,a['path']).read_bytes()

    def assemble(self,root_gameobject: str) -> dict:
        if root_gameobject not in self.idx.objects or self.idx.objects[root_gameobject]['type']!='GameObject':
            raise ContentError('Choose an explicit GameObject asset ID from the catalog')
        result={'schema':1,'coordinates':'unity-left-handed','source_status':self.catalog['source_status'],
                'nodes':[],'meshes':{},'materials':{},'images':{},'skins':{},'animations':[],
                'unsupported':self.unsupported,'retained_only':[]}
        def components(go):
            values=[]
            for entry in self.tree(go)['m_Component']:
                target=self.ptr(go,entry['component'])
                if target is None:raise ContentError('Null component on '+go)
                values.append(target)
            return values
        def transform(go):
            found=[c for c in components(go) if self.idx.objects[c]['type']=='Transform']
            if len(found)!=1:raise ContentError('Expected exactly one Transform on '+go)
            return found[0]
        root=transform(root_gameobject)
        if self.ptr(root,self.tree(root)['m_Father']) is not None:
            raise ContentError('Select a hierarchy root; ancestor transforms cannot be discarded')
        pending=[(root,None)];visited=set();renderers=[]
        while pending:
            tr,parent=pending.pop()
            if tr in visited:raise ContentError('Duplicate/cyclic Transform child')
            visited.add(tr);tree=self.tree(tr);go=self.ptr(tr,tree['m_GameObject'])
            if go is None:raise ContentError('Transform has no GameObject')
            got=self.tree(go)
            if not got.get('m_IsActive',True):self.unsupported.append('inactive GameObject: '+go)
            node={'id':tr,'name':got.get('m_Name',tr),'parent':parent,
                  'translation':_vector(tree['m_LocalPosition'],3),
                  'rotation':_vector(tree['m_LocalRotation'],4),'scale':_vector(tree['m_LocalScale'],3)}
            filters=[]
            for c in components(go):
                typ=self.idx.objects[c]['type'];data=self.tree(c)
                if typ=='MeshFilter':filters.append(self.ptr(c,data['m_Mesh']))
                elif typ in ('MeshRenderer','SkinnedMeshRenderer'):renderers.append((node,c))
                elif typ in ('Animator','Animation','LODGroup','ParticleSystem','ParticleSystemRenderer'):
                    self.unsupported.append(typ+' requires runtime conversion: '+c)
                elif typ not in ('Transform',):
                    result['retained_only'].append({'asset':c,'type':typ,'reason':'raw component, not applied to visual GLB'})
            if len(filters)>1:raise ContentError('Multiple MeshFilters on one GameObject')
            node['_mesh_filter']=filters[0] if filters else None
            result['nodes'].append(node)
            for ptr in reversed(tree.get('m_Children',[])):
                child=self.ptr(tr,ptr)
                if child is None or self.ptr(child,self.tree(child)['m_Father'])!=tr:raise ContentError('Inconsistent Transform parent/child relationship')
                pending.append((child,tr))
        for node,key in renderers:
            if node.get('mesh'):raise ContentError('Multiple renderers on a single Transform')
            data=self.tree(key);typ=self.idx.objects[key]['type']
            if not data.get('m_Enabled',True):self.unsupported.append('disabled renderer: '+key)
            if data.get('m_LightmapIndex',65535) not in (-1,65535,4294967295):self.unsupported.append('baked lightmap renderer: '+key)
            mesh=self.ptr(key,data['m_Mesh']) if typ=='SkinnedMeshRenderer' else node['_mesh_filter']
            if not mesh:raise ContentError('Renderer is missing its mesh')
            decoded=decode_tree(json.loads(self.artifact(mesh,'decoded-mesh')))
            if any(decoded.get('uv'+str(i)) for i in range(2,8)):self.unsupported.append('additional UV channels need Godot policy: '+mesh)
            mt=self.tree(mesh)
            if mt.get('m_Shapes',{}).get('channels'):self.unsupported.append('blend shapes not converted: '+mesh)
            result['meshes'][mesh]=decoded;node['mesh']=mesh;node['materials']=[]
            for ptr in data['m_Materials']:
                material=self.ptr(key,ptr)
                if material is None:raise ContentError('Null renderer material')
                node['materials'].append(material)
                if material not in result['materials']:self.material(material,result)
            if typ=='SkinnedMeshRenderer':
                bones=[self.ptr(key,p) for p in data['m_Bones']]
                if not bones or any(v not in visited for v in bones):raise ContentError('Skin references bones outside selected hierarchy')
                matrices=[_matrix(m) for m in mt['m_BindPose']]
                skin={'joints':bones,'inverse_bind_matrices':matrices}
                skeleton=self.ptr(key,data['m_RootBone'])
                if skeleton is not None:
                    if skeleton not in visited:raise ContentError('Skeleton root is outside hierarchy')
                    skin['skeleton']=skeleton
                result['skins'][key]=skin;node['skin']=key
        for n in result['nodes']:n.pop('_mesh_filter',None)
        result['retained_only'].sort(key=lambda r:r['asset'])
        self.unsupported.sort()
        return result

    def material(self,key: str,result: dict):
        tree=self.tree(key);shader=self.ptr(key,tree['m_Shader'])
        if shader is None:raise ContentError('Material has no shader')
        st=self.tree(shader);name=st.get('m_ParsedForm',{}).get('m_Name') or st.get('m_Name')
        if name not in ('Universal Render Pipeline/Lit','Universal Render Pipeline/Unlit'):
            raise ContentError('Unsupported source shader: '+str(name))
        saved=tree['m_SavedProperties'];floats=_pairs(saved.get('m_Floats',[]));colors=_pairs(saved.get('m_Colors',[]));textures=_pairs(saved.get('m_TexEnvs',[]))
        color=colors.get('_BaseColor',colors.get('_Color'))
        if color is None:raise ContentError('Material base color must come from recovered properties/shader defaults')
        mat={'shader':name,'base_color':[color[k] for k in ('r','g','b','a')],
             'double_sided':floats.get('_Cull',2)==0}
        if name.endswith('/Lit'):
            if '_Metallic' not in floats or '_Smoothness' not in floats:raise ContentError('Missing recovered Lit scalar properties')
            mat.update(metallic=floats['_Metallic'],roughness=1-floats['_Smoothness'])
        else:mat.update(metallic=0,roughness=1)
        if floats.get('_AlphaClip',0):mat.update(alpha_mode='MASK',alpha_cutoff=floats['_Cutoff'])
        elif floats.get('_Surface',0):
            if floats.get('_Blend',0)!=0:raise ContentError('Unsupported non-alpha transparent blend')
            mat['alpha_mode']='BLEND'
        else:mat['alpha_mode']='OPAQUE'
        chosen='_BaseMap' if '_BaseMap' in textures else '_MainTex'
        for prop,tex in textures.items():
            image=self.ptr(key,tex['m_Texture'])
            if image is None:continue
            if prop!=chosen:
                self.unsupported.append('texture channel '+prop+' requires shader mapping: '+key)
                continue
            png=self.artifact(image,'decoded-image')
            result['images'][image]={'png':base64.b64encode(png).decode('ascii')}
            mat.update(base_texture=image,uv_scale=_vector(tex['m_Scale'],2),uv_offset=_vector(tex['m_Offset'],2))
        result['materials'][key]=mat


def _vector(v: dict | list, size: int) -> list:
    return list(v) if isinstance(v,(list,tuple)) else [v[k] for k in ('x','y','z','w')[:size]]


def _matrix(v: dict | list) -> list:
    return list(v) if isinstance(v,(list,tuple)) else [v[f'e{r}{c}'] for c in range(4) for r in range(4)]


def _pairs(v: dict | list) -> dict:
    if isinstance(v,dict):return v
    return dict((p['first'],p['second']) if isinstance(p,dict) else p for p in v)
