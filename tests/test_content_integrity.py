"""Adversarial content validation: corrupt metadata must not become green evidence."""
import base64
import json
import tempfile
import unittest
from pathlib import Path
from tools.content_pipeline import ContentError,verify_catalog,canonical
from tools.content_unity import inventory
from tools.content_gltf import compile_scene
from tests.content_fixture import bundle_fixture,scene_fixture

class IntegrityTests(unittest.TestCase):
    def catalog(self):
        d=tempfile.TemporaryDirectory();self.addCleanup(d.cleanup)
        root=Path(d.name)/'input';out=Path(d.name)/'out'
        loader,decoder,_=bundle_fixture(root)
        return out,inventory(root,out,loader=loader,mesh_decoder=decoder)

    def test_tampered_stable_id_rejected(self):
        out,c=self.catalog();c['objects'][0]['id']='0'*64
        (out/'catalog.json').write_bytes(canonical(c))
        with self.assertRaises(ContentError):verify_catalog(out)

    def test_pointer_edge_must_match_preserved_typetree(self):
        out,c=self.catalog()
        renderer=next(o for o in c['objects'] if o['type']=='SkinnedMeshRenderer')
        mesh=next(r for r in renderer['references'] if r['field']=='/m_Mesh')
        mesh['asset']=next(o['id'] for o in c['objects'] if o['type']=='Material')
        (out/'catalog.json').write_bytes(canonical(c))
        with self.assertRaises(ContentError):verify_catalog(out)

    def test_png_crc_failure_is_rejected_before_publishing_glb(self):
        s=scene_fixture();png=bytearray(base64.b64decode(s['images']['pixel']['png']));png[29]^=1
        s['images']['pixel']['png']=base64.b64encode(png).decode()
        with self.assertRaises(ContentError):compile_scene(s)

    def test_png_truncation_is_rejected(self):
        s=scene_fixture();s['images']['pixel']['png']=base64.b64encode(base64.b64decode(s['images']['pixel']['png'])[:-8]).decode()
        with self.assertRaises(ContentError):compile_scene(s)

    def test_animation_timestamps_that_collapse_to_float32_are_rejected(self):
        s=scene_fixture();s['animations'][0]['channels'][0]['times']=[1.0,1.0+1e-12]
        with self.assertRaises(ContentError):compile_scene(s)

    def test_skeleton_root_must_be_ancestor_of_its_bones(self):
        s=scene_fixture();s['skins']['rig']['skeleton']='render'
        with self.assertRaises(ContentError):compile_scene(s)

    def test_hdr_vertex_colors_not_silently_clamped(self):
        s=scene_fixture();s['meshes']['mesh']['colors']=[[4,1,1,1]]*3
        with self.assertRaises(ContentError):compile_scene(s)

    def test_incomplete_catalog_cannot_claim_indexed_success(self):
        out,c=self.catalog();c['objects'][0]['errors'].append({'field':'','reason':'unsupported decode'})
        (out/'catalog.json').write_bytes(canonical(c))
        with self.assertRaises(ContentError):verify_catalog(out)

    def test_catalog_object_count_cannot_be_fabricated(self):
        out,c=self.catalog();c['counts']['objects']+=1
        (out/'catalog.json').write_bytes(canonical(c))
        with self.assertRaises(ContentError):verify_catalog(out)

    def test_empty_catalog_cannot_claim_success(self):
        out,c=self.catalog();c.update(files=[],objects=[],counts={'bundles':2,'objects':0,'types':{},'errors':0})
        (out/'catalog.json').write_bytes(canonical(c))
        with self.assertRaises(ContentError):verify_catalog(out)

    def test_triangles_that_collapse_after_float32_conversion_are_rejected(self):
        s=scene_fixture();s['meshes']['mesh']['positions']=[[1e8,0,0],[1e8+1,0,0],[1e8,1,0]]
        with self.assertRaises(ContentError):compile_scene(s)

    def test_accessor_bounds_match_actual_float32_values(self):
        import struct
        from tools.content_gltf import Builder
        builder=Builder();index=builder.accessor([[0.1,0.2,0.3]],'VEC3',bounds=True)
        actual=[struct.unpack('<f',struct.pack('<f',v))[0] for v in (0.1,0.2,0.3)]
        self.assertEqual(builder.g['accessors'][index]['min'],actual)
        self.assertEqual(builder.g['accessors'][index]['max'],actual)

    def test_external_file_name_collision_inside_same_bundle_is_not_a_guess(self):
        from tools.content_pipeline import AssetIndex
        files=[{'key':'a/CAB-a','bundle':'a','name':'CAB-a','externals':['archive:/CAB-b/CAB-b']},
               {'key':'a/CAB-b','bundle':'a','name':'CAB-b','externals':[]},
               {'key':'b/CAB-b','bundle':'b','name':'CAB-b','externals':[]}]
        objects=[{'file':f['key'],'path_id':'7','type':'Mesh'} for f in files]
        with self.assertRaisesRegex(ContentError,'ambiguous'):
            AssetIndex(files,objects).resolve('a/CAB-a',{'m_FileID':1,'m_PathID':7})
