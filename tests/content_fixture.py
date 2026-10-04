"""Tiny authored conversion fixture. NOT a game asset; never used by the game."""
from __future__ import annotations
import base64
import struct
import zlib


def png_pixel() -> bytes:
    def chunk(tag,data):
        return struct.pack('>I',len(data))+tag+data+struct.pack('>I',zlib.crc32(tag+data)&0xffffffff)
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',1,1,8,6,0,0,0))+chunk(b'IDAT',zlib.compress(bytes([0,235,80,40,255])))+chunk(b'IEND',b'')


def scene_fixture() -> dict:
    identity=[1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1]
    return {'schema':1,'coordinates':'unity-left-handed','source_status':'synthetic-test-fixture','unsupported':[],
        'nodes':[
            {'id':'root','name':'FixtureRoot','parent':None,'translation':[2,3,4]},
            {'id':'bone','name':'FixtureBone','parent':'root'},
            {'id':'render','name':'FixtureRender','parent':'root','mesh':'mesh','skin':'rig','materials':['paint']}],
        'meshes':{'mesh':{'positions':[[0,0,0],[1,0,0],[0,1,0]],'normals':[[0,0,1]]*3,
            'tangents':[[1,0,0,1]]*3,'uv0':[[0,0],[1,0],[0,1]],'submeshes':[[0,1,2]],
            'joints':[[0,0,0,0]]*3,'weights':[[1,0,0,0]]*3}},
        'skins':{'rig':{'joints':['bone'],'skeleton':'bone','inverse_bind_matrices':[identity]}},
        'materials':{'paint':{'shader':'Universal Render Pipeline/Lit','base_color':[1,1,1,1],
            'metallic':0.0,'roughness':0.6,'base_texture':'pixel','alpha_mode':'MASK','alpha_cutoff':0.4}},
        'images':{'pixel':{'png':base64.b64encode(png_pixel()).decode()}},
        'animations':[{'name':'FixtureMotion','channels':[{'node':'bone','path':'translation',
            'times':[0.0,1.0],'values':[[0,0,0],[1,0,0]],'interpolation':'LINEAR'}]}]}


def bundle_fixture(root):
    """Fake reader objects exercising the real inventory/assembly adapter boundary."""
    from types import SimpleNamespace as NS
    from tools.content_pipeline import canonical,encode_tree
    root.mkdir(exist_ok=True)
    (root/'scene.bundle').write_bytes(b'fixture scene input - NOT a Unity bundle')
    (root/'assets.bundle').write_bytes(b'fixture asset input - NOT a Unity bundle')
    (root/'catalog.json').write_text('{"synthetic":"root"}\n')
    (root/'Master.bank').write_bytes(b'fixture bank bytes - NOT playable audio')
    (root/'scene.bundle:com.apple.provenance').write_bytes(b'sidecar')
    sf=NS(name='CAB-scene',externals=[NS(path='archive:/CAB-assets/CAB-assets')])
    af=NS(name='CAB-assets',externals=[])
    def ptr(pid,fid=0):return {'m_FileID':fid,'m_PathID':pid}
    def vector(v):return dict(zip(('x','y','z','w'),v))
    class Image:
        size=(1,1)
        def convert(self,mode):
            assert mode=='RGBA';return self
        def save(self,stream,**kwargs):stream.write(png_pixel())
    class Obj:
        def __init__(self,file,pid,typ,tree):
            self.assets_file=file;self.path_id=pid;self.type=NS(name=typ);self.tree=tree
        def get_raw_data(self):return canonical(encode_tree(self.tree))
        def parse_as_dict(self):return self.tree
        def parse_as_object(self):return NS(image=Image())
    def go(pid,name,components):return Obj(sf,pid,'GameObject',{'m_Name':name,'m_IsActive':True,'m_Component':[{'component':ptr(c)} for c in components]})
    def tr(pid,goid,parent,children,position=[0,0,0]):
        return Obj(sf,pid,'Transform',{'m_GameObject':ptr(goid),'m_Father':ptr(parent),'m_Children':[ptr(c) for c in children],
            'm_LocalPosition':vector(position),'m_LocalRotation':vector([0,0,0,1]),'m_LocalScale':vector([1,1,1])})
    scene=[go(1,'FixtureRoot',[2]),tr(2,1,0,[4,6],[2,3,4]),go(3,'FixtureBone',[4]),tr(4,3,2,[]),
           go(5,'FixtureRender',[6,7]),tr(6,5,2,[]),
           Obj(sf,7,'SkinnedMeshRenderer',{'m_Mesh':ptr(7,1),'m_Materials':[ptr(8,1)],'m_Bones':[ptr(4)],'m_RootBone':ptr(4),'m_Enabled':True,'m_LightmapIndex':65535})]
    assets=[Obj(af,7,'Mesh',{'m_Name':'duplicate','m_BindPose':[scene_fixture()['skins']['rig']['inverse_bind_matrices'][0]],'m_Shapes':{'channels':[]}}),
            Obj(af,8,'Material',{'m_Name':'duplicate','m_Shader':ptr(9),'m_SavedProperties':{
                'm_Colors':[['_BaseColor',dict(zip(('r','g','b','a'),[1,1,1,1]))]],
                'm_Floats':[['_Metallic',0],['_Smoothness',.4],['_AlphaClip',1],['_Cutoff',.4]],
                'm_TexEnvs':[['_BaseMap',{'m_Texture':ptr(10),'m_Scale':vector([1,1]),'m_Offset':vector([0,0])}]]}}),
            Obj(af,9,'Shader',{'m_ParsedForm':{'m_Name':'Universal Render Pipeline/Lit'}}),
            Obj(af,10,'Texture2D',{'m_Name':'duplicate'})]
    by_name={'scene.bundle':scene,'assets.bundle':assets}
    return lambda p:NS(objects=by_name[__import__('pathlib').Path(p).name]),lambda o:scene_fixture()['meshes']['mesh'],by_name


def main():
    import argparse
    from pathlib import Path
    from tools.content_gltf import export_glb
    p=argparse.ArgumentParser(description='Generate only the authored test fixture, never production game content')
    p.add_argument('output',type=Path)
    args=p.parse_args()
    print(export_glb(scene_fixture(),args.output))

if __name__=='__main__':main()
