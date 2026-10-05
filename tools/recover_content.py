#!/usr/bin/env python3
"""Read-only source gates, deterministic content recovery and explicit GLB export."""
from __future__ import annotations
import argparse
import json
import sys
import signal
from pathlib import Path
try:
    from .content_pipeline import (ContentError, DMG_SHA256, DMG_BYTES, canonical, make_requirements,
        verify_source, verify_catalog, resolve_requirements)
    from .content_unity import inventory, SceneReader
    from .content_gltf import export_glb
except ImportError:
    from content_pipeline import (ContentError, DMG_SHA256, DMG_BYTES, canonical, make_requirements,
        verify_source, verify_catalog, resolve_requirements)
    from content_unity import inventory, SceneReader
    from content_gltf import export_glb


def write_new(path: Path, value: dict):
    if path.exists():raise ContentError('Existing evidence is not overwritten: '+str(path))
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('xb') as f:f.write(canonical(value))


def main(argv=None) -> int:
    p=argparse.ArgumentParser(description=__doc__)
    commands=p.add_subparsers(dest='command',required=True)
    source=commands.add_parser('verify-dmg',help='Reject pointer/missing/corrupt original inputs')
    source.add_argument('path',type=Path)
    plan=commands.add_parser('requirements',help='Extract actual definition requirements, not fictional resources')
    plan.add_argument('--definitions',type=Path,default=Path(__file__).resolve().parents[1]/'work/assets/definitions/definitions.json')
    plan.add_argument('--track',default='Map_Race_ArlenSpeedway');plan.add_argument('--character',default='Character_KH_Hank');plan.add_argument('--kart',default='Kart_KH_LandryLonghorner')
    plan.add_argument('--output',type=Path,required=True)
    scan=commands.add_parser('inventory',help='Read actual extracted AssetBundles; does not download or decrypt them')
    scan.add_argument('source',type=Path);scan.add_argument('output',type=Path)
    scan.add_argument('--checkpoints', type=Path, help='Private per-bundle checkpoint directory; enables bounded workers')
    scan.add_argument('--resume', action='store_true', help='Verify and reuse an existing compatible checkpoint job')
    scan.add_argument('--bundle-timeout', type=float, default=120)
    scan.add_argument('--budget', type=float, default=1800, help='Decoder/finalizer execution budget in seconds')
    scan.add_argument('--max-bundles', type=int)
    scan.add_argument('--progress-report', type=Path, help='Metadata-only progress report; never contains raw payloads')
    scan.add_argument('--continue-until-complete', action='store_true', help='Continue timeout/budget pauses within the same job')
    scan.add_argument('--pass-budget', type=float, help='Per-pass scheduling slice; requires --continue-until-complete')
    scan.add_argument('--max-passes', type=int, help='Maximum same-job passes, default 8')
    scan.add_argument('--max-bundle-timeout', type=float, help='Capped worker backoff, default 480 seconds')
    scan.add_argument('--stall-limit', type=int, help='Stop after this many unchanged follow-up passes, default 2')
    scan.add_argument('--continuation-report', type=Path, help='New metadata-only pass history outside checkpoints')
    verify=commands.add_parser('verify-catalog');verify.add_argument('catalog',type=Path)
    bind=commands.add_parser('bind',help='Validate explicit analyst bindings against hashed catalog evidence')
    bind.add_argument('--catalog',type=Path,required=True);bind.add_argument('--requirements',type=Path,required=True)
    bind.add_argument('--bindings',type=Path,required=True);bind.add_argument('--output',type=Path,required=True)
    export=commands.add_parser('export-root',help='Convert a supported explicit hierarchy; unsupported content fails')
    export.add_argument('--catalog',type=Path,required=True);export.add_argument('--root',required=True);export.add_argument('--output',type=Path,required=True)
    normalized=commands.add_parser('export-scene',help='Convert explicitly decoded Unity-space scene JSON')
    normalized.add_argument('scene',type=Path);normalized.add_argument('output',type=Path)
    args=p.parse_args(argv)
    try:
        if args.command=='verify-dmg':report=verify_source(args.path,expected_sha256=DMG_SHA256,expected_size=DMG_BYTES)
        elif args.command=='requirements':
            report=make_requirements(args.definitions,track=args.track,character=args.character,kart=args.kart);write_new(args.output,report)
        elif args.command=='inventory':
            continuation_options = (args.pass_budget, args.max_passes, args.max_bundle_timeout,
                                    args.stall_limit, args.continuation_report)
            if any(v is not None for v in continuation_options) and not args.continue_until_complete:
                raise ContentError('Continuation controls require --continue-until-complete')
            if args.continue_until_complete and not args.checkpoints:
                raise ContentError('Continuation controls require --checkpoints')
            if args.continue_until_complete and args.max_bundles is not None:
                raise ContentError('--max-bundles is a single-pass control, incompatible with continuation')
            if args.checkpoints:
                # Running as a direct script still needs the repository root for
                # the module worker's imports, independent of the caller's cwd.
                root = str(Path(__file__).resolve().parents[1])
                if root not in sys.path: sys.path.insert(0, root)
                from tools.content_batches import inventory_batched
                if args.continue_until_complete:
                    from tools.content_continuation import inventory_continued
                    report=inventory_continued(args.source,args.output,args.checkpoints,
                        resume=args.resume,bundle_timeout=args.bundle_timeout,budget=args.budget,
                        pass_budget=args.pass_budget if args.pass_budget is not None else 900,
                        max_passes=args.max_passes if args.max_passes is not None else 8,
                        max_bundle_timeout=args.max_bundle_timeout if args.max_bundle_timeout is not None else 480,
                        stall_limit=args.stall_limit if args.stall_limit is not None else 2,
                        report_path=args.progress_report,continuation_report=args.continuation_report)
                else:
                    report=inventory_batched(args.source,args.output,args.checkpoints,
                        resume=args.resume,bundle_timeout=args.bundle_timeout,budget=args.budget,
                        max_bundles=args.max_bundles,report_path=args.progress_report)
            elif (args.resume or args.max_bundles is not None or args.progress_report
                  or args.bundle_timeout != 120 or args.budget != 1800):
                raise ContentError('Inventory controls require --checkpoints')
            else:report=inventory(args.source,args.output)
        elif args.command=='verify-catalog':report=verify_catalog(args.catalog)
        elif args.command=='bind':
            report=resolve_requirements(json.loads(args.requirements.read_text()),verify_catalog(args.catalog),json.loads(args.bindings.read_text()));write_new(args.output,report)
        elif args.command=='export-root':
            scene=SceneReader(args.catalog).assemble(args.root);report=export_glb(scene,args.output)
            report['retained_only']=scene['retained_only']
            # Sidecar explicitly records components not applied to visual GLB.
            args.output.with_suffix('.glb.json').write_bytes(canonical(report))
        else:report=export_glb(json.loads(args.scene.read_text()),args.output)
        print(json.dumps({k:v for k,v in report.items() if k not in ('objects','files','sources','requirements','bindings')},indent=2))
        return 1 if report.get('status')=='incomplete' else 0
    except (ContentError,KeyError,OSError,ValueError,TypeError) as e:
        print('CONTENT GATE FAILED: '+str(e),file=sys.stderr)
        return 1

def _cancel(_signal, _frame):
    # Let the active bounded-worker context terminate its own process group.
    raise KeyboardInterrupt

if __name__=='__main__':
    signal.signal(signal.SIGTERM, _cancel)
    try: raise SystemExit(main())
    except KeyboardInterrupt: raise SystemExit(130)
