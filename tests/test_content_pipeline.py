"""Synthetic fixtures test the converter, never claim recovered game media."""
from __future__ import annotations
import copy
import json
import tempfile
import unittest
from pathlib import Path
from tools.content_pipeline import (AssetIndex, ContentError, canonical, encode_tree,
    decode_tree, make_requirements, verify_source, safe_child)
from tools.content_gltf import export_glb, inspect_glb
from tests.content_fixture import scene_fixture

ROOT = Path(__file__).resolve().parents[1]

class IdentityTests(unittest.TestCase):
    def index(self):
        return AssetIndex([
            {'key':'a.bundle/CAB-a','bundle':'a.bundle','name':'CAB-a','externals':['archive:/CAB-b/CAB-b']},
            {'key':'b.bundle/CAB-b','bundle':'b.bundle','name':'CAB-b','externals':[]}],
            [{'file':'a.bundle/CAB-a','path_id':'9007199254740993','type':'GameObject','name':'duplicate'},
             {'file':'a.bundle/CAB-a','path_id':'7','type':'Material','name':'duplicate'},
             {'file':'b.bundle/CAB-b','path_id':'7','type':'Texture2D','name':'duplicate'}])

    def test_external_pointer_does_not_resolve_in_local_id_space(self):
        idx=self.index()
        local=idx.resolve('a.bundle/CAB-a',{'m_FileID':0,'m_PathID':7})
        other=idx.resolve('a.bundle/CAB-a',{'m_FileID':1,'m_PathID':7})
        self.assertNotEqual(local,other)
        self.assertEqual(idx.objects[other]['type'],'Texture2D')

    def test_large_negative_and_positive_ids_are_not_float_coerced(self):
        idx=self.index()
        self.assertEqual(idx.objects[idx.resolve('a.bundle/CAB-a',{'m_FileID':0,'m_PathID':9007199254740993})]['path_id'],'9007199254740993')
        self.assertIsNone(idx.resolve('a.bundle/CAB-a',{'m_FileID':0,'m_PathID':0}))

    def test_missing_and_invalid_external_are_errors(self):
        idx=self.index()
        for ptr in ({'m_FileID':2,'m_PathID':7},{'m_FileID':1,'m_PathID':99},
                    {'m_FileID':-1,'m_PathID':7},{'m_FileID':0,'m_PathID':True}):
            with self.subTest(ptr=ptr),self.assertRaises(ContentError):idx.resolve('a.bundle/CAB-a',ptr)

    def test_ambiguous_external_is_not_first_match(self):
        idx=self.index()
        files=list(idx.files.values())+[{'key':'c.bundle/CAB-b','bundle':'c.bundle','name':'CAB-b','externals':[]}]
        with self.assertRaisesRegex(ContentError,'ambiguous'):
            AssetIndex(files,list(idx.objects.values())).resolve('a.bundle/CAB-a',{'m_FileID':1,'m_PathID':7})

    def test_duplicate_identity_rejected(self):
        idx=self.index()
        with self.assertRaises(ContentError):AssetIndex(list(idx.files.values()),list(idx.objects.values())*2)

    def test_identity_does_not_depend_on_iteration_or_display_name(self):
        idx=self.index(); records=list(idx.objects.values()); records[0]['name']='renamed'
        other=AssetIndex(list(reversed(list(idx.files.values()))),list(reversed(records)))
        self.assertEqual(set(idx.objects),set(other.objects))

    def test_raw_tree_preserves_bytes_integer_and_pointer_fields(self):
        raw={'blob':b'\x00\xff\x80','n':-9185864079050272049,'p':{'m_FileID':1,'m_PathID':7},'empty':{}}
        self.assertEqual(decode_tree(json.loads(canonical(encode_tree(raw)))),raw)
        self.assertIn('$int64',canonical(encode_tree(raw)).decode())

    def test_nonfinite_and_unknown_values_are_not_stringified(self):
        for value in (float('nan'),float('inf'),object()):
            with self.assertRaises(ContentError):encode_tree(value)

    def test_safe_paths_reject_traversal_absolute_and_symlink(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            for path in ('../escape','/etc/passwd','a/../../b','C:\\escape','a\\b'):
                with self.subTest(path=path),self.assertRaises(ContentError):safe_child(root,path)
            (root/'link').symlink_to('/tmp')
            with self.assertRaises(ContentError):safe_child(root,'link/bad')

    def test_pointer_file_cannot_be_claimed_as_original_dmg(self):
        with tempfile.TemporaryDirectory() as directory:
            pointer=Path(directory)/'source.dmg'
            pointer.write_text('version https://git-lfs.github.com/spec/v1\noid sha256:'+('0'*64)+'\nsize 1190250225\n')
            with self.assertRaisesRegex(ContentError,'LFS pointer'):
                verify_source(pointer,expected_sha256='0'*64,expected_size=1190250225)

    def test_actual_file_hash_and_size_are_checked(self):
        import hashlib
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'input';p.write_bytes(b'abc')
            self.assertEqual(verify_source(p,expected_sha256=hashlib.sha256(b'abc').hexdigest(),expected_size=3)['bytes'],3)
            with self.assertRaises(ContentError):verify_source(p,expected_sha256='0'*64,expected_size=3)

class RequirementTests(unittest.TestCase):
    def test_manifest_uses_actual_definition_ids_guids_scenes_and_audio(self):
        p=make_requirements(ROOT/'work/assets/definitions/definitions.json')
        self.assertEqual(p['status'],'requirements-only')
        self.assertFalse(p['original_media_verified'])
        self.assertIn('Wheels_KH_Stock_LandryLonghorner',p['definitions'])
        fields={r['field']:r for r in p['requirements'] if r['definition']=='Character_KH_Hank'}
        self.assertEqual(fields['/_assetRef']['guid'],'e2e28c6886d8885478abfb21f768ae2a')
        self.assertIn('SC_Race_ArlenSpeedway_Gameplay_Variant_A',[r.get('scene') for r in p['requirements']])
        self.assertIn('event:/Music/KOH/KOH_Race_2',[r.get('event') for r in p['requirements']])
        self.assertTrue(all(r['status']=='unresolved' for r in p['requirements']))

    def test_unknown_definition_is_not_substituted(self):
        with self.assertRaises(ContentError):make_requirements(ROOT/'work/assets/definitions/definitions.json',character='not-a-character')

    def test_requirements_repeat_byte_identically(self):
        path=ROOT/'work/assets/definitions/definitions.json'
        self.assertEqual(canonical(make_requirements(path)),canonical(make_requirements(path)))

    def test_committed_arlen_requirements_reproduce_byte_identically(self):
        actual=canonical(make_requirements(ROOT/'work/assets/definitions/definitions.json'))
        self.assertEqual((ROOT/'docs/status/2026-10-04-arlen-content-requirements.json').read_bytes(),actual)

class ExportTests(unittest.TestCase):
    def export(self,scene=None):
        d=tempfile.TemporaryDirectory();self.addCleanup(d.cleanup)
        p=Path(d.name)/'scene.glb'; report=export_glb(scene or scene_fixture(),p)
        return p,report,inspect_glb(p)

    def test_real_glb_header_chunks_skin_animation_and_material(self):
        p,r,(g,b)=self.export()
        self.assertGreater(p.stat().st_size,200)
        self.assertEqual(len(g['skins']),1)
        self.assertEqual(len(g['animations']),1)
        self.assertEqual(g['materials'][0]['alphaMode'],'MASK')
        self.assertIn('baseColorTexture',g['materials'][0]['pbrMetallicRoughness'])
        self.assertEqual(r['status'],'converted-not-game-verified')
        self.assertFalse(r['original_game_assets_verified'])

    def test_export_is_deterministic_under_mapping_reordering(self):
        s=scene_fixture();p,_,_=self.export(s)
        s['nodes']=list(reversed(s['nodes']));s['meshes']=dict(reversed(list(s['meshes'].items())))
        q,_,_=self.export(s)
        self.assertEqual(p.read_bytes(),q.read_bytes())

    def test_source_coordinate_conversion_is_applied_once(self):
        from tools.content_gltf import read_accessor
        p,r,(g,b)=self.export()
        mesh=g['meshes'][0]['primitives'][0]
        self.assertEqual(read_accessor(g,b,mesh['attributes']['POSITION'])[1],[-1.0,0.0,0.0])
        self.assertEqual(read_accessor(g,b,mesh['indices']),[[0],[2],[1]])
        self.assertEqual(read_accessor(g,b,mesh['attributes']['TEXCOORD_0'])[0],[0.0,1.0])
        root=next(n for n in g['nodes'] if n['name']=='FixtureRoot')
        self.assertEqual(root['translation'],[-2.0,3.0,4.0])

    def test_invalid_geometry_is_rejected(self):
        for field,value in [('positions',[]),('normals',[[0,0,1]]),('submeshes',[[0,1,999]]),('positions',[[float('nan'),0,0]]*3)]:
            s=scene_fixture();s['meshes']['mesh'][field]=value
            with self.subTest(field=field),self.assertRaises(ContentError):self.export(s)

    def test_invalid_skin_rejected(self):
        for field,value in [('joints',[[9,0,0,0]]*3),('weights',[[0,0,0,0]]*3),('weights',[[1.1,-.1,0,0]]*3)]:
            s=scene_fixture();s['meshes']['mesh'][field]=value
            with self.subTest(field=field),self.assertRaises(ContentError):self.export(s)

    def test_missing_material_texture_joint_and_parent_are_errors(self):
        for area,key in [('materials','paint'),('images','pixel')]:
            s=scene_fixture();del s[area][key]
            with self.assertRaises(ContentError):self.export(s)
        s=scene_fixture();s['nodes'][1]['parent']='missing'
        with self.assertRaises(ContentError):self.export(s)
        s=scene_fixture();s['skins']['rig']['joints']=['missing']
        with self.assertRaises(ContentError):self.export(s)

    def test_parent_cycle_rejected(self):
        s=scene_fixture();s['nodes'][0]['parent']='bone'
        with self.assertRaisesRegex(ContentError,'cycle'):self.export(s)

    def test_bad_animations_are_not_silently_dropped(self):
        for times in ([1,0],[0,0],[0,float('inf')]):
            s=scene_fixture();s['animations'][0]['channels'][0]['times']=times
            with self.assertRaises(ContentError):self.export(s)

    def test_unsupported_animation_or_shader_blocks_export(self):
        s=scene_fixture();s['unsupported']=['humanoid muscle clip not decoded']
        with self.assertRaisesRegex(ContentError,'unsupported'):self.export(s)
        s=scene_fixture();s['materials']['paint']['shader']='unknown/custom'
        with self.assertRaises(ContentError):self.export(s)

    def test_output_is_not_overwritten_and_failure_leaves_no_glb(self):
        p,_,_=self.export()
        with self.assertRaises(ContentError):export_glb(scene_fixture(),p)
        s=scene_fixture();s['meshes']['mesh']['positions']=[]
        with tempfile.TemporaryDirectory() as d:
            q=Path(d)/'bad.glb'
            with self.assertRaises(ContentError):export_glb(s,q)
            self.assertFalse(q.exists())


class InventoryTests(unittest.TestCase):
    def prepare(self,mutate=None):
        from tools.content_unity import inventory
        from tests.content_fixture import bundle_fixture
        d=tempfile.TemporaryDirectory();self.addCleanup(d.cleanup)
        base=Path(d.name);root=base/'input';out=base/'inventory'
        loader,decoder,objects=bundle_fixture(root)
        if mutate:mutate(objects)
        cat=inventory(root,out,loader=loader,mesh_decoder=decoder)
        return base,out,cat,objects

    def test_real_adapter_contract_records_source_hashes_types_and_external_edges(self):
        from tools.content_pipeline import verify_catalog
        base,out,cat,objs=self.prepare()
        self.assertEqual(cat['status'],'indexed')
        self.assertEqual(cat['counts']['bundles'],2)
        self.assertEqual(cat['counts']['objects'],11)
        self.assertEqual(cat['source_status'],'synthetic-adapter-fixture')
        self.assertTrue(all(':com.apple.' not in f['path'] for f in cat['sources']))
        self.assertEqual(verify_catalog(out),cat)
        renderer=next(r for r in cat['objects'] if r['type']=='SkinnedMeshRenderer')
        mesh=next(r for r in cat['objects'] if r['type']=='Mesh')
        self.assertIn({'field':'/m_Mesh','asset':mesh['id'],'file_id':1,'path_id':'7'},renderer['references'])

    def test_inventory_to_scene_to_glb_is_connected(self):
        from tools.content_unity import SceneReader
        base,out,cat,_=self.prepare()
        root=next(r['id'] for r in cat['objects'] if r['name']=='FixtureRoot')
        scene=SceneReader(out).assemble(root)
        self.assertEqual(len(scene['skins']),1)
        self.assertEqual(len(scene['images']),1)
        self.assertEqual(scene['unsupported'],[])
        report=export_glb(scene,base/'assembled.glb')
        self.assertEqual(report['counts']['vertices'],3)
        self.assertEqual(report['counts']['triangles'],1)

    def test_mutated_payload_is_rejected(self):
        from tools.content_pipeline import verify_catalog
        _,out,cat,_=self.prepare()
        a=cat['objects'][0]['artifacts'][0]
        (out/a['path']).write_bytes(b'tampered')
        with self.assertRaises(ContentError):verify_catalog(out)

    def test_broken_reference_keeps_diagnostics_but_blocks_conversion(self):
        from tools.content_unity import SceneReader
        def mutate(objs):objs['scene.bundle'][-1].tree['m_Mesh']['m_FileID']=9
        _,out,cat,_=self.prepare(mutate)
        self.assertEqual(cat['status'],'incomplete')
        root=next(r['id'] for r in cat['objects'] if r['name']=='FixtureRoot')
        with self.assertRaises(ContentError):SceneReader(out).assemble(root)

    def test_unsupported_rig_animation_is_not_reported_as_imported(self):
        from tools.content_unity import SceneReader
        def mutate(objs):
            renderer=objs['scene.bundle'][-1]
            extra=type(renderer)(renderer.assets_file,8,'Animator',{'m_Controller':{'m_FileID':0,'m_PathID':0}})
            objs['scene.bundle'].append(extra)
            objs['scene.bundle'][0].tree['m_Component'].append({'component':{'m_FileID':0,'m_PathID':8}})
        base,out,cat,_=self.prepare(mutate)
        root=next(r['id'] for r in cat['objects'] if r['name']=='FixtureRoot')
        scene=SceneReader(out).assemble(root)
        self.assertIn('Animator',str(scene['unsupported']))
        with self.assertRaises(ContentError):export_glb(scene,base/'cannot-publish.glb')
        self.assertFalse((base/'cannot-publish.glb').exists())

    def test_empty_input_is_not_success_and_must_not_make_output(self):
        from tools.content_unity import inventory
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'empty';root.mkdir();out=Path(temp)/'out'
            with self.assertRaises(ContentError):inventory(root,out)
            self.assertFalse(out.exists())

    def test_inventory_repeats_byte_identically(self):
        from tools.content_unity import inventory
        from tests.content_fixture import bundle_fixture
        base,out,cat,_=self.prepare()
        loader,decoder,_=bundle_fixture(base/'input')
        second=base/'second';inventory(base/'input',second,loader=loader,mesh_decoder=decoder)
        files=lambda p:{x.relative_to(p).as_posix():x.read_bytes() for x in p.rglob('*') if x.is_file()}
        self.assertEqual(files(out),files(second))

    def test_explicit_bindings_require_source_hash_and_do_not_claim_import(self):
        from tools.content_pipeline import resolve_requirements
        _,_,cat,_=self.prepare()
        key='requirement';asset=next(r['id'] for r in cat['objects'] if r['name']=='FixtureRoot')
        plan={'requirements':[{'key':key,'kind':'scene','scene':'FixtureRoot'}]}
        source=next(s for s in cat['sources'] if s['path']=='catalog.json')
        binding={'key':key,'asset':asset,'evidence':{'file':'catalog.json','sha256':source['sha256'],'source_key':'synthetic/root'}}
        report=resolve_requirements(plan,cat,[binding])
        self.assertEqual(report['status'],'bound-not-imported')
        self.assertFalse(report['original_media_verified'])
        self.assertEqual(len(report['bindings'][key]['closure']),11)
        binding['evidence']['sha256']='0'*64
        with self.assertRaises(ContentError):resolve_requirements(plan,cat,[binding])

if __name__=='__main__':unittest.main()
