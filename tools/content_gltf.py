"""Deterministic GLB export of explicitly decoded Unity-space content.

This is NOT an IL2CPP or animation-blob decoder. Unsupported source features
must be listed in `unsupported` and fail conversion. No default gray material,
empty mesh, invented animation, or guessed skeleton is substituted.

Coordinates: C=diag(-1,1,1); node transforms and bind matrices use C*M*C.
Triangle winding reverses. PNG pixels use top-left convention, so UV V flips.
The spatial and UV reflections cancel in tangent handedness (w is unchanged).
"""
from __future__ import annotations
import base64
import hashlib
import json
import math
import struct
import zlib
from pathlib import Path
from typing import Any
try:
    from .content_pipeline import ContentError, canonical
except ImportError:
    from content_pipeline import ContentError, canonical

COMPONENTS={'SCALAR':1,'VEC2':2,'VEC3':3,'VEC4':4,'MAT4':16}
FORMATS={5126:('f',4),5125:('I',4),5123:('H',2)}


def rows(value: Any, width: int, label: str, *, count: int | None=None, integers: bool=False) -> list[list]:
    if not isinstance(value,(list,tuple)) or not value or count is not None and len(value)!=count:
        raise ContentError('Missing or inconsistent '+label)
    result=[]
    for row in value:
        if not isinstance(row,(list,tuple)) or len(row)!=width:
            raise ContentError('Invalid '+label+' dimensions')
        if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in row):
            raise ContentError('Non-finite or nonnumeric '+label)
        if integers and any(not isinstance(v,int) or v<0 or v>4294967295 for v in row):
            raise ContentError('Invalid integer '+label)
        result.append(list(row))
    return result


def vec(v: Any, width: int, label: str) -> list:
    return rows([v],width,label)[0]


def norm(v: list) -> float:
    return math.sqrt(sum(x*x for x in v))


def rotation(v: list) -> list:
    v=vec(v,4,'rotation')
    if abs(norm(v)-1)>1e-4:
        raise ContentError('Quaternion is not normalized')
    return [v[0],-v[1],-v[2],v[3]]


def point(v: list) -> list:
    return [-v[0],v[1],v[2]]


def bind_matrix(m: list) -> list:
    m=vec(m,16,'column-major inverse bind matrix')
    if any(abs(m[i])>1e-5 for i in (3,7,11)) or abs(m[15]-1)>1e-5:
        raise ContentError('Inverse bind matrix is not affine')
    det=(m[0]*(m[5]*m[10]-m[9]*m[6])-m[4]*(m[1]*m[10]-m[9]*m[2])+m[8]*(m[1]*m[6]-m[5]*m[2]))
    if abs(det)<1e-12:
        raise ContentError('Singular inverse bind matrix')
    return [v*(-1 if (i%4==0) != (i//4==0) else 1) for i,v in enumerate(m)]


class Builder:
    def __init__(self):
        self.data=bytearray()
        self.g={'asset':{'version':'2.0','generator':'Kart Lab content converter v1'},
                'buffers':[{'byteLength':0}],'bufferViews':[],'accessors':[],
                'nodes':[],'meshes':[],'materials':[],'images':[],'textures':[],
                'samplers':[{'magFilter':9729,'minFilter':9987,'wrapS':10497,'wrapT':10497}],
                'skins':[],'animations':[]}

    def block(self, content: bytes, target: int | None=None) -> int:
        self.data.extend(b'\0'*(-len(self.data)%4))
        entry={'buffer':0,'byteOffset':len(self.data),'byteLength':len(content)}
        if target is not None:entry['target']=target
        self.g['bufferViews'].append(entry);self.data.extend(content)
        return len(self.g['bufferViews'])-1

    def accessor(self, data: list[list], kind: str, component: int=5126, target: int | None=None, bounds: bool=False) -> int:
        data=rows(data,COMPONENTS[kind],kind,integers=component!=5126)
        char,_=FORMATS[component]
        try:content=b''.join(struct.pack('<'+char*len(row),*row) for row in data)
        except (OverflowError,struct.error) as e:raise ContentError('Accessor exceeds component representation') from e
        # Float32 overflow may round to infinity on some runtimes.
        if component==5126 and any(not math.isfinite(v[0]) for v in struct.iter_unpack('<f',content)):
            raise ContentError('Accessor overflows float32')
        item={'bufferView':self.block(content,target),'componentType':component,'count':len(data),'type':kind}
        if bounds:
            # Bounds describe the stored values, not unrepresentable float64 inputs.
            stored=list(struct.iter_unpack('<'+char*COMPONENTS[kind],content))
            item['min']=[min(r[i] for r in stored) for i in range(COMPONENTS[kind])]
            item['max']=[max(r[i] for r in stored) for i in range(COMPONENTS[kind])]
        self.g['accessors'].append(item)
        return len(self.g['accessors'])-1

    def bytes(self) -> bytes:
        self.g['buffers'][0]['byteLength']=len(self.data)
        # Empty optional arrays are invalid in glTF. No references target them.
        for name in ('skins','animations','images','textures','materials','meshes','samplers'):
            if not self.g[name] or name=='samplers' and not self.g.get('textures'):
                self.g.pop(name,None)
        meta=canonical(self.g).rstrip(b'\n');meta+=b' '*(-len(meta)%4)
        binary=bytes(self.data);binary+=b'\0'*(-len(binary)%4)
        return struct.pack('<III',0x46546c67,2,12+8+len(meta)+8+len(binary))+struct.pack('<II',len(meta),0x4e4f534a)+meta+struct.pack('<II',len(binary),0x004e4942)+binary


def validate_png(png: bytes) -> tuple[int,int]:
    """Validate the adapter's RGBA8 PNG contract, including CRC and pixel budget."""
    if png[:8]!=b'\x89PNG\r\n\x1a\n':raise ContentError('Image is not a PNG')
    offset=8;tags=[];idat=bytearray();width=height=0
    while offset<len(png):
        if offset+12>len(png):raise ContentError('Truncated PNG chunk')
        size=struct.unpack_from('>I',png,offset)[0];tag=png[offset+4:offset+8]
        if offset+12+size>len(png):raise ContentError('PNG chunk exceeds image bounds')
        data=png[offset+8:offset+8+size]
        crc=struct.unpack_from('>I',png,offset+8+size)[0]
        if zlib.crc32(tag+data)&0xffffffff!=crc:raise ContentError('PNG CRC mismatch')
        if tag==b'IHDR':
            if tags or size!=13:raise ContentError('Invalid PNG header ordering')
            width,height,depth,color,compression,filter_type,interlace=struct.unpack('>IIBBBBB',data)
            if width<1 or height<1 or width*height>67108864:raise ContentError('Empty or oversized texture')
            if (depth,color,compression,filter_type,interlace)!=(8,6,0,0,0):raise ContentError('Expected decoded, noninterlaced RGBA8 PNG')
        elif tag==b'IDAT':
            if not tags or b'IEND' in tags:raise ContentError('Invalid PNG image-data ordering')
            if b'IDAT' in tags and tags[-1]!=b'IDAT':raise ContentError('Noncontiguous PNG image data')
            idat.extend(data)
        elif tag==b'IEND':
            if size!=0 or offset+12!=len(png):raise ContentError('Invalid PNG end chunk')
        elif not tag or not tag[0]&32:
            raise ContentError('Unsupported critical PNG chunk')
        tags.append(tag);offset+=12+size
    if not tags or tags[0]!=b'IHDR' or tags[-1]!=b'IEND' or b'IDAT' not in tags:raise ContentError('Incomplete PNG')
    expected=height*(1+4*width)
    try:
        decoder=zlib.decompressobj();pixels=decoder.decompress(bytes(idat),expected+1)
    except zlib.error as e:raise ContentError('Invalid PNG compression') from e
    if len(pixels)!=expected or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
        raise ContentError('PNG pixels do not match declared dimensions')
    if any(pixels[i]>4 for i in range(0,len(pixels),1+4*width)):raise ContentError('Invalid PNG scanline filter')
    return width,height


def compile_scene(scene: dict) -> tuple[bytes,dict]:
    if scene.get('schema')!=1 or scene.get('coordinates')!='unity-left-handed':
        raise ContentError('Unsupported scene schema/coordinate convention')
    if scene.get('unsupported'):
        raise ContentError('Source contains unsupported features: '+str(scene['unsupported']))
    b=Builder();g=b.g
    nodes=scene.get('nodes',[])
    if not nodes:raise ContentError('Scene has no nodes')
    by_id={n['id']:n for n in nodes}
    if len(by_id)!=len(nodes):raise ContentError('Duplicate scene node identity')
    order=sorted(by_id); ni={key:i for i,key in enumerate(order)}
    for key in order:
        seen=set();cur=key
        while cur is not None:
            if cur not in by_id:raise ContentError('Missing scene parent: '+cur)
            if cur in seen:raise ContentError('Scene parent cycle')
            seen.add(cur);cur=by_id[cur].get('parent')
    # Resource names are display-only; stable IDs, not names, determine output.
    images={}
    for key,entry in sorted(scene.get('images',{}).items()):
        try:png=base64.b64decode(entry['png'],validate=True)
        except (ValueError,KeyError) as e:raise ContentError('Invalid PNG payload') from e
        width,height=validate_png(png)
        if entry.get('sha256') and hashlib.sha256(png).hexdigest()!=entry['sha256']:
            raise ContentError('PNG payload hash mismatch')
        index=len(g['images']);g['images'].append({'name':key,'mimeType':'image/png','bufferView':b.block(png)})
        images[key]=len(g['textures']);g['textures'].append({'source':index,'sampler':0})
    materials={}
    for key,mat in sorted(scene.get('materials',{}).items()):
        if mat.get('shader') not in ('Universal Render Pipeline/Lit','Universal Render Pipeline/Unlit'):
            raise ContentError('Unsupported source shader: '+str(mat.get('shader')))
        color=vec(mat['base_color'],4,'base color')
        if any(v<0 or v>1 for v in color):raise ContentError('Base color is outside glTF normalized range')
        pbr={'baseColorFactor':color}
        for src,dst in [('metallic','metallicFactor'),('roughness','roughnessFactor')]:
            v=mat[src]
            if not isinstance(v,(float,int)) or not math.isfinite(v) or not 0<=v<=1:
                raise ContentError('Invalid '+src)
            pbr[dst]=v
        if mat.get('base_texture'):
            if mat['base_texture'] not in images:raise ContentError('Missing material texture')
            info={'index':images[mat['base_texture']]}
            offset=vec(mat.get('uv_offset',[0,0]),2,'UV offset');scale=vec(mat.get('uv_scale',[1,1]),2,'UV scale')
            if offset!=[0,0] or scale!=[1,1]:
                # v' = 1-v; convert Unity offset to glTF's upper-left convention.
                info['extensions']={'KHR_texture_transform':{'offset':[offset[0],1-scale[1]-offset[1]],'scale':scale}}
                g.setdefault('extensionsUsed',[]).append('KHR_texture_transform')
            pbr['baseColorTexture']=info
        mode=mat.get('alpha_mode','OPAQUE')
        if mode not in ('OPAQUE','MASK','BLEND'):raise ContentError('Unsupported alpha mode')
        item={'name':key,'pbrMetallicRoughness':pbr,'alphaMode':mode,'doubleSided':bool(mat.get('double_sided',False))}
        if mode=='MASK':
            cutoff=mat['alpha_cutoff']
            if not isinstance(cutoff,(int,float)) or not math.isfinite(cutoff) or not 0<=cutoff<=1:raise ContentError('Invalid alpha cutoff')
            item['alphaCutoff']=cutoff
        if mat['shader'].endswith('/Unlit'):
            item['extensions']={'KHR_materials_unlit':{}};g.setdefault('extensionsUsed',[]).append('KHR_materials_unlit')
        materials[key]=len(g['materials']);g['materials'].append(item)
    skins={}
    for key,skin in sorted(scene.get('skins',{}).items()):
        joints=skin['joints'];matrices=skin['inverse_bind_matrices']
        if not joints or len(set(joints))!=len(joints) or any(j not in ni for j in joints) or len(matrices)!=len(joints):
            raise ContentError('Invalid skin joints/bind matrix correspondence')
        item={'name':key,'joints':[ni[j] for j in joints],'inverseBindMatrices':b.accessor([bind_matrix(m) for m in matrices],'MAT4')}
        if skin.get('skeleton'):
            if skin['skeleton'] not in ni:raise ContentError('Missing skeleton root')
            for joint in joints:
                cur=joint
                while cur is not None and cur!=skin['skeleton']:
                    cur=by_id[cur].get('parent')
                if cur is None:raise ContentError('Skeleton root is not an ancestor of every joint')
            item['skeleton']=ni[skin['skeleton']]
        skins[key]=len(g['skins']);g['skins'].append(item)
    mesh_uses={};stats={'mesh_instances':0,'vertices':0,'triangles':0,'skins':len(skins),'animations':len(scene.get('animations',[])),'materials':len(materials),'images':len(images),'nodes':len(nodes)}
    for key in order:
        n=by_id[key]
        trans=vec(n.get('translation',[0,0,0]),3,'translation')
        scale=vec(n.get('scale',[1,1,1]),3,'scale')
        if any(abs(v)<1e-12 for v in scale):raise ContentError('Zero scale scene node')
        item={'name':n.get('name',key),'translation':point(trans),'rotation':rotation(n.get('rotation',[0,0,0,1])),'scale':scale,
              'extras':{'source_id':key}}
        children=[ni[c] for c in order if by_id[c].get('parent')==key]
        if children:item['children']=children
        if n.get('mesh'):
            mesh_id=n['mesh'];m=scene.get('meshes',{}).get(mesh_id)
            if not m:raise ContentError('Missing mesh: '+mesh_id)
            positions=rows(m.get('positions'),3,'positions');count=len(positions)
            try:
                positions=[list(struct.unpack('<fff',struct.pack('<fff',*v))) for v in positions]
            except (OverflowError,struct.error) as e:
                raise ContentError('Positions exceed float32 representation') from e
            if any(not math.isfinite(v) for row in positions for v in row):
                raise ContentError('Positions overflow float32')
            mat_ids=n.get('materials',[])
            submeshes=m.get('submeshes',[])
            if not submeshes or len(mat_ids)!=len(submeshes) or any(mid not in materials for mid in mat_ids):
                raise ContentError('Missing or mismatched renderer material slots')
            skin_key=n.get('skin')
            if skin_key and skin_key not in skins:raise ContentError('Missing renderer skin')
            has_weights=bool(m.get('weights')) or bool(m.get('joints'))
            if bool(skin_key)!=has_weights:raise ContentError('Skinned mesh/renderer mismatch')
            use=(mesh_id,tuple(mat_ids),skin_key)
            if use not in mesh_uses:
                attrs={'POSITION':b.accessor([point(v) for v in positions],'VEC3',target=34962,bounds=True)}
                for src,dst,width in [('normals','NORMAL',3),('tangents','TANGENT',4),('uv0','TEXCOORD_0',2),('uv1','TEXCOORD_1',2),('colors','COLOR_0',4)]:
                    if m.get(src) is not None:
                        vals=rows(m[src],width,src,count=count)
                        if src=='normals':
                            if any(abs(norm(v)-1)>1e-3 for v in vals):raise ContentError('Nonunit mesh normal')
                            vals=[point(v) for v in vals]
                        elif src=='tangents':
                            if any(abs(norm(v[:3])-1)>1e-3 or abs(abs(v[3])-1)>1e-3 for v in vals):raise ContentError('Invalid tangent basis')
                            vals=[point(v[:3])+[v[3]] for v in vals]
                        elif src=='colors' and any(v<0 or v>1 for row in vals for v in row):
                            raise ContentError('Vertex colors exceed glTF normalized range')
                        elif src.startswith('uv'):vals=[[v[0],1-v[1]] for v in vals]
                        attrs[dst]=b.accessor(vals,'VEC'+str(width),target=34962)
                if any(scene['materials'][mid].get('base_texture') for mid in mat_ids) and 'TEXCOORD_0' not in attrs:
                    raise ContentError('Textured mesh has no UV0')
                if skin_key:
                    js=rows(m['joints'],4,'joints',count=count,integers=True)
                    ws=rows(m['weights'],4,'weights',count=count)
                    joint_count=len(scene['skins'][skin_key]['joints'])
                    if joint_count>65535 or any(j>=joint_count for row in js for j in row):raise ContentError('Bone index outside skin')
                    if any(any(w<0 for w in row) or abs(sum(row)-1)>1e-4 for row in ws):raise ContentError('Invalid skin weights')
                    attrs['JOINTS_0']=b.accessor(js,'VEC4',5123,34962)
                    attrs['WEIGHTS_0']=b.accessor(ws,'VEC4',target=34962)
                primitives=[]
                for indices,mid in zip(submeshes,mat_ids):
                    if not indices or len(indices)%3:raise ContentError('Empty/nontriangle primitive')
                    if any(isinstance(i,bool) or not isinstance(i,int) or i<0 or i>=count for i in indices):raise ContentError('Index outside vertex array')
                    out=[]
                    for at in range(0,len(indices),3):
                        a,c,d=indices[at:at+3]
                        u=[positions[c][i]-positions[a][i] for i in range(3)];v=[positions[d][i]-positions[a][i] for i in range(3)]
                        cross=[u[1]*v[2]-u[2]*v[1],u[2]*v[0]-u[0]*v[2],u[0]*v[1]-u[1]*v[0]]
                        if norm(cross)<1e-12:raise ContentError('Degenerate mesh triangle')
                        out.extend([[a],[d],[c]])
                    primitives.append({'attributes':attrs,'indices':b.accessor(out,'SCALAR',5125,34963),'material':materials[mid],'mode':4})
                    stats['triangles']+=len(indices)//3
                mesh_uses[use]=len(g['meshes']);g['meshes'].append({'name':mesh_id,'primitives':primitives});stats['vertices']+=count
            item['mesh']=mesh_uses[use];stats['mesh_instances']+=1
            if skin_key:item['skin']=skins[skin_key]
        elif n.get('skin'):raise ContentError('Skin without a mesh')
        g['nodes'].append(item)
    for animation in sorted(scene.get('animations',[]),key=lambda a:a['name']):
        out={'name':animation['name'],'channels':[],'samplers':[]};targets=set()
        for channel in sorted(animation['channels'],key=lambda c:(c['node'],c['path'])):
            path=channel['path'];node=channel['node'];method=channel.get('interpolation','LINEAR')
            if node not in ni or path not in ('translation','rotation','scale') or method not in ('LINEAR','STEP','CUBICSPLINE'):
                raise ContentError('Unsupported animation target/interpolation')
            if (node,path) in targets:raise ContentError('Duplicate animation target')
            targets.add((node,path))
            times=rows([[t] for t in channel['times']],1,'animation times')
            if times[0][0]<0 or any(a[0]>=c[0] for a,c in zip(times,times[1:])):raise ContentError('Animation time is not strictly increasing')
            try:
                rounded=[struct.unpack('<f',struct.pack('<f',r[0]))[0] for r in times]
            except (OverflowError,struct.error) as e:raise ContentError('Animation time exceeds float32') from e
            if any(not math.isfinite(v) for v in rounded) or any(a>=c for a,c in zip(rounded,rounded[1:])):
                raise ContentError('Animation times collapse in float32 representation')
            times=[[v] for v in rounded]
            width=4 if path=='rotation' else 3
            vals=rows(channel['values'],width,'animation values',count=len(times)*(3 if method=='CUBICSPLINE' else 1))
            converted=[]
            for i,v in enumerate(vals):
                is_tangent=method=='CUBICSPLINE' and i%3!=1
                if path=='translation':v=point(v)
                elif path=='rotation':v=[v[0],-v[1],-v[2],v[3]] if is_tangent else rotation(v)
                converted.append(v)
            if method!='CUBICSPLINE' and path=='rotation':
                # Quaternion sign continuity is mathematically equivalent, not a
                # change to the recovered orientation. Cubic slopes are untouched.
                for i in range(1,len(converted)):
                    if sum(a*c for a,c in zip(converted[i-1],converted[i]))<0:converted[i]=[-v for v in converted[i]]
            sampler=len(out['samplers'])
            out['samplers'].append({'input':b.accessor(times,'SCALAR',bounds=True),'output':b.accessor(converted,'VEC'+str(width)),'interpolation':method})
            out['channels'].append({'sampler':sampler,'target':{'node':ni[node],'path':path}})
        if not out['channels']:raise ContentError('Empty animation')
        g['animations'].append(out)
    if not stats['mesh_instances']:raise ContentError('Scene has no rendered geometry')
    g['scenes']=[{'nodes':[ni[k] for k in order if by_id[k].get('parent') is None]}];g['scene']=0
    if 'extensionsUsed' in g:g['extensionsUsed']=sorted(set(g['extensionsUsed']))
    return b.bytes(),stats


def inspect_glb(path: Path) -> tuple[dict,bytes]:
    content=path.read_bytes()
    if len(content)<28:raise ContentError('Truncated GLB')
    magic,version,size=struct.unpack_from('<III',content)
    if magic!=0x46546c67 or version!=2 or size!=len(content):raise ContentError('Invalid GLB header')
    offset=12;chunks=[]
    while offset<len(content):
        if offset+8>len(content):raise ContentError('Truncated GLB chunk')
        count,kind=struct.unpack_from('<II',content,offset);offset+=8
        if count%4 or offset+count>len(content):raise ContentError('Invalid GLB chunk bounds')
        chunks.append((kind,content[offset:offset+count]));offset+=count
    if [c[0] for c in chunks]!=[0x4e4f534a,0x004e4942]:raise ContentError('Unexpected GLB chunks')
    return json.loads(chunks[0][1]),chunks[1][1]


def read_accessor(g: dict, binary: bytes, index: int) -> list[list]:
    a=g['accessors'][index];v=g['bufferViews'][a['bufferView']]
    char,size=FORMATS[a['componentType']];width=COMPONENTS[a['type']]
    start=v.get('byteOffset',0)+a.get('byteOffset',0);stride=v.get('byteStride',width*size)
    if start+(a['count']-1)*stride+width*size>v.get('byteOffset',0)+v['byteLength']:raise ContentError('Accessor exceeds buffer view')
    return [list(struct.unpack_from('<'+char*width,binary,start+i*stride)) for i in range(a['count'])]


def export_glb(scene: dict, output: Path) -> dict:
    sidecar=output.with_suffix('.glb.json')
    if output.suffix!='.glb' or output.exists() or sidecar.exists():raise ContentError('Use a new .glb output path')
    payload,stats=compile_scene(scene)
    output.parent.mkdir(parents=True,exist_ok=True)
    report={'schema':1,'status':'converted-not-game-verified','original_game_assets_verified':False,
            'source_status':scene.get('source_status','unspecified'),'input_sha256':hashlib.sha256(canonical(scene)).hexdigest(),
            'sha256':hashlib.sha256(payload).hexdigest(),'bytes':len(payload),'counts':stats,
            'coordinate_policy':'mirror-X; reverse-winding; flip-UV-V; C*M*C bind matrices',
            'limitations':['Conversion is not proof of original-game fidelity or Android playability.',
                           'URP-to-glTF material mapping, sampler defaults and color interpretation are explicit approximations; source shaders are not reproduced.']}
    # All validation precedes writes. Exclusive creation protects old evidence.
    with output.open('xb') as f:f.write(payload)
    try:
        g,binary=inspect_glb(output)
        for i in range(len(g['accessors'])):read_accessor(g,binary,i)
        with sidecar.open('xb') as f:f.write(canonical(report))
    except Exception:
        output.unlink(missing_ok=True)
        raise
    return report
