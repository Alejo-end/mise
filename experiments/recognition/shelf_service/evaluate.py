"""Separate crop identity and frame counting reports. No evaluation data trains the gallery.

Run: UV_PROJECT=shelf_service uv run python -m shelf_service.evaluate --synthetic
Real: ... --store shelf_service/store --recordings /path/to/data/recordings/evaluation
"""
import argparse
import json
import platform
from pathlib import Path
from time import perf_counter

import numpy as np
from PIL import Image, ImageEnhance, ImageDraw

from .calibration import classify
from .detection import surface_pixels
from .embedding import DinoEmbedder
from .service import ShelfService
from .storage import ARCHIVED_IDENTITIES as IDENTITIES, INTEGRATION, ROOT, Store, image_hash, write_json  # archived props set


def crop_decision(service, image):
    bg, meta = service.store.background()
    vectors, labels, thresholds = service.gallery(bg, meta)
    return classify(service.embed(image), vectors, labels, thresholds)


def table_summary(identities, counts):
    return {
        'identity_per_crop': {'n': len(identities), 'correct': sum(r['correct'] for r in identities),
                              'unknown_predictions': sum(r['status']=='unknown' for r in identities)},
        'visible_units_per_frame': {'n':len(counts), 'exact':sum(r['exact'] for r in counts),
                                   'uncertain_frames':sum(r['uncertain'] for r in counts),
                                   'absolute_count_error':sum(sum(abs(r['predicted'].get(i,0)-r['expected'].get(i,0)) for i in IDENTITIES) for r in counts)}
    }


def identity_row(service, image, truth, sample, **metadata):
    decision = crop_decision(service, image)
    expected = truth if truth in IDENTITIES else 'unknown'
    predicted = decision['identity'] or decision['status']
    return {'sample':sample, 'truth':truth, 'expected':expected, 'predicted':predicted,
            'correct': predicted == expected, **decision, **metadata}


def count_row(result, expected, sample, **metadata):
    predicted = result['visible_units']
    return {'sample':sample, 'expected':expected, 'predicted':predicted,
            'exact': all(predicted.get(i,0)==expected.get(i,0) for i in IDENTITIES),
            'uncertain':result['uncertain'], **metadata}


def timing_summary(results):
    return {stage: {'p50':float(np.percentile([r['timing_ms'][stage] for r in results],50)),
                    'p95':float(np.percentile([r['timing_ms'][stage] for r in results],95))}
            for stage in ['detect','embed','match','total']} if results else {}


def synthetic(service, steady):
    """Cup/keyboard/desk textures are identity proxies, never real product labels."""
    source = Image.open(steady).convert('RGB')
    background = ImageEnhance.Brightness(source.resize((480,360))).enhance(.06)
    patches = {
        'exp:milk': source.crop((270,50,400,270)).resize((65,100)),
        'exp:chips': source.crop((65,80,170,170)).resize((75,70)),
        'exp:energy-drink': source.crop((155,105,210,215)).resize((45,85)),
        'exp:fitness-bar': source.crop((85,290,230,345)).resize((90,35)),
    }
    # Ensure each synthetic package is one foreground component, including dark texture pixels.
    patches={identity: Image.fromarray(np.maximum(np.asarray(patch),60).astype(np.uint8))
             for identity,patch in patches.items()}
    surface=[0,0,1,1]
    service.set_background(background,surface,'2026-11-14T10:00:00Z')
    # Distinct brightness variants and positions are generated before evaluation.
    for identity in IDENTITIES:
        for j, factor in enumerate([.94,1.0,1.06]):
            frame=background.copy()
            patch=ImageEnhance.Brightness(patches[identity]).enhance(factor)
            frame.paste(patch,(80+j*15,120))
            service.enrol(identity,frame,surface,'reference',f'2026-11-14T10:00:0{j}Z','synthetic-ref')
    cases=[('moved-left',[('exp:milk',45,140)]), ('moved-right',[('exp:milk',335,145)]),
           ('unfamiliar',[('exp:fitness-bar',175,160)]),
           ('two-visible',[('exp:milk',65,130),('exp:milk',280,130)]),
           ('one-visible',[('exp:milk',280,130)]), ('hand',[]), ('empty',[]),
           ('chips',[('exp:chips',200,180)]), ('drink',[('exp:energy-drink',200,180)])]
    identities, counts, results, by_name = [], [], [], {}
    for name, objects in cases:
        frame=background.copy()
        expected={i:0 for i in IDENTITIES}
        crops=[]
        for identity,x,y in objects:
            patch=ImageEnhance.Brightness(patches[identity]).enhance(1.02)
            frame.paste(patch,(x,y))
            if identity in IDENTITIES:
                expected[identity]+=1
            crops.append((patch,identity))
        if name=='hand':
            ImageDraw.Draw(frame).rectangle((190,0,255,230),fill=(190,125,100))
        result=service.recognize(frame,surface,'2026-11-14T10:01:00Z')
        results.append(result)
        for patch,identity in crops:
            identities.append(identity_row(service,patch,identity,name,experiment_label='synthetic'))
        by_name[name]=result
        if name != 'hand':
            counts.append(count_row(result,expected,name))
    # Several repeated warm observations measure latency; they are not extra independent samples.
    for _ in range(6):
        frame=background.copy()
        frame.paste(patches['exp:milk'],(205,140))
        results.append(service.recognize(frame,surface,'2026-11-14T10:02:00Z'))
    demos={
        'moved_product_keeps_identity':all(r['objects'] and r['objects'][0]['identity']=='exp:milk' for r in [by_name['moved-left'],by_name['moved-right']]),
        'unfamiliar_proxy_not_milk': all(o['identity']!='exp:milk' for o in by_name['unfamiliar']['objects']) and bool(by_name['unfamiliar']['objects']),
        'remove_one_of_two':by_name['two-visible']['visible_units']['exp:milk']==2 and by_name['one-visible']['visible_units']['exp:milk']==1,
        'hand_proxy_obstructed_uncertain':by_name['hand']['uncertain'] and any(o['status']=='obstructed' for o in by_name['hand']['objects']),
    }
    return {'experiment_label':'synthetic', 'note':'Steady-01 cup/desk texture composites with invented identity proxies; NOT product-recognition accuracy, NOT a real fitness-bar test.',
            'source':str(steady), 'model':service.embed.info, 'platform':platform.platform(),
            'model_load_ms':service.embed.load_ms, 'thresholds':results[0]['thresholds'],
            **table_summary(identities,counts), 'demonstrations':demos,
            'latency_ms':timing_summary(results[1:]), 'one_object_warm_ms':timing_summary(results[-6:]), 'first_recognition_ms':results[0]['timing_ms'],
            'latency_observations':len(results)-1, 'identity_rows':identities,'count_rows':counts}


def safe_file(directory, name):
    path=(directory/name).resolve()
    if not path.is_relative_to(directory.resolve()) or not path.is_file():
        raise ValueError('frame path is missing or outside recording directory')
    return path


def validated_recordings(root, reference_sessions):
    accepted,rejected=[],[]
    for path in sorted(Path(root).glob('*/labels.json')):
        try:
            labels=json.loads(path.read_text())
            if labels.get('validated') is not True or labels.get('validation_source')!='human' or not str(labels.get('validated_by','')).strip():
                raise ValueError('human validation required; model labels are proposals')
            if labels.get('role')!='evaluation' or not labels.get('session_id'):
                raise ValueError('evaluation role and session_id required')
            if labels['session_id'] in reference_sessions:
                raise ValueError('session overlaps reference gallery')
            if not labels.get('frames') and not labels.get('labels'):
                raise ValueError('need labelled frames or slot windows')
            windows=[]
            for window in labels.get('labels',[]):
                if window.get('validated',True) is not True or window['from_t']>=window['to_t']:
                    raise ValueError('invalid or unvalidated window')
                for prior in windows:
                    if window['from_t']<prior['to_t'] and prior['from_t']<window['to_t'] and set(window['slots'])&set(prior['slots']):
                        raise ValueError('overlapping windows')
                windows.append(window)
            accepted.append((path.parent,labels))
        except (OSError,ValueError,KeyError,TypeError) as exc:
            rejected.append({'recording':path.parent.name,'reason':str(exc)})
    return accepted,rejected


def recording_frames(directory, labels):
    """Adapt native object labels or integration's human-confirmed per-slot windows.

    Slot rectangles are used only to extract *evaluation* crops, never as identity priors.
    Count scoring requires explicit counts for all slots; empty/unknown windows are not guessed.
    """
    if labels.get('frames'):
        for entry in labels['frames']:
            if entry.get('validated', True) is not True:
                continue
            yield {'path':safe_file(directory,entry['file']), 'objects':entry.get('objects',[]),
                   'expected_counts':entry.get('visible_units'), 'surface':entry.get('surface',labels.get('surface')),
                   'scenario':entry.get('scenario',labels.get('scenario')), 'file':entry['file'],
                   'expected_obstructed':entry.get('obstructed') is True}
        return
    config=json.loads((directory/'config.json').read_text())
    slots={str(s['slot']):s['rect'] for s in config['slots']}
    frames=[json.loads(line) for line in (directory/'meta.jsonl').read_text().splitlines() if line.strip()]
    for frame in frames:
        if frame.get('kind')!='frame':
            continue
        truths,counts,actual={},{},{}
        for window in labels['labels']:
            if window['from_t']<=frame['t']<window['to_t']:
                truths.update(window['slots'])
                counts.update(window.get('counts',{}))
                actual.update(window.get('identities',{}))
        objects=[]
        for slot,truth in truths.items():
            if slot in slots and counts.get(slot)==1 and truth not in ('empty','obstructed'):
                objects.append({'box':slots[slot], 'identity':actual.get(slot) or truth})
        expected=None
        if set(slots)<=set(counts) and all(isinstance(counts[s],int) and counts[s]>=0 for s in slots):
            expected={i:0 for i in IDENTITIES}
            for slot in slots:
                identity=actual.get(slot) or truths.get(slot)
                if identity in IDENTITIES:
                    expected[identity]+=counts[slot]
        if objects or expected is not None or 'obstructed' in truths.values():
            yield {'path':safe_file(directory,'frames/'+frame['file']), 'objects':objects,
                   'expected_counts':expected,'surface':labels.get('surface'),
                   'scenario':labels.get('scenario'), 'file':frame['file'],
                   'expected_obstructed':'obstructed' in truths.values()}


def evaluate_real(service, recordings):
    references=service.store.references()
    reference_sessions={p['session_id'] for _,p in references if p.get('session_id')}
    accepted,rejected=validated_recordings(recordings,reference_sessions)
    identities,counts,results,observations=[],[],[],[]
    if not references or not (service.store.root/'background.jpg').exists():
        return {'experiment_label':'real','state':'idle','reason':'need manual reference gallery and empty-surface background',
                **table_summary([],[]),'demonstrations':{k:'idle: no validated evidence' for k in ['moved_product','fitness_bar_not_milk','remove_one_of_two','hand']},'rejected':rejected}
    bg,bg_meta=service.store.background()
    vectors,labels,thresholds=service.gallery(bg,bg_meta)
    if not thresholds['ready']:
        return {'experiment_label':'real','state':'idle','reason':thresholds['reason'],**table_summary([],[]),'rejected':rejected}
    seen_ref_hashes={p['frame_fingerprint'] for _,p in references}
    seen_ref_hashes.update(image_hash(Image.open(service.store.root/p['frame_path']).convert('RGB')) for _,p in references)
    for identity in IDENTITIES:
        for photo in service.store.meta(identity)['photos']:
            if photo['role']!='heldout':
                continue
            if photo.get('session_id') in reference_sessions:
                rejected.append({'sample':photo['id'],'reason':'heldout session overlaps reference'});continue
            identities.append(identity_row(service,Image.open(service.store.root/photo['path']),identity,photo['id'],source='manual-heldout'))
    for directory,lab in accepted:
        checkpoint=(len(identities),len(counts),len(results),len(observations))
        try:
            # Materialise first so malformed labels cannot leave a partially scored recording.
            rows=list(recording_frames(directory,lab))
            for row in rows:
                frame=Image.open(row['path']).convert('RGB')
                if image_hash(frame) in seen_ref_hashes:
                    rejected.append({'recording':directory.name,'sample':row['file'],'reason':'frame duplicates a reference'});continue
                sample=directory.name+'/'+row['file']
                for obj in row['objects']:
                    if obj.get('validated',True) is not True:
                        continue
                    box=obj['box']
                    if len(box)!=4 or any(not np.isfinite(v) for v in box) or min(box)<0 or box[2]<=0 or box[3]<=0 or box[0]+box[2]>1 or box[1]+box[3]>1:
                        raise ValueError('invalid normalised object box')
                    identities.append(identity_row(service,frame.crop(surface_pixels(frame,box)),obj['identity'],sample,source='human-labelled-recording'))
                result=service.recognize(frame,row['surface'] or bg_meta['surface'],lab.get('captured_at','recording:'+sample))
                results.append(result)
                if row['expected_counts'] is not None:
                    if any(not isinstance(v,int) or v<0 for v in row['expected_counts'].values()):
                        raise ValueError('invalid explicit visible-unit counts')
                    counts.append(count_row(result,row['expected_counts'],sample))
                observations.append({'scenario':row['scenario'],'sample':sample,'result':result,
                                     'truth_objects':row['objects'],'expected_counts':row['expected_counts'],
                                     'expected_obstructed':row['expected_obstructed']})
        except (OSError,ValueError,KeyError,TypeError) as exc:
            for collection,length in zip((identities,counts,results,observations),checkpoint):
                del collection[length:]
            rejected.append({'recording':directory.name,'reason':str(exc)})
    return {'experiment_label':'real','state':'measured' if identities or counts else 'idle',
            'note':'Only manual heldout photos and human-validated recordings; no model-generated ground truth.',
            **table_summary(identities,counts),'identity_rows':identities,'count_rows':counts,
            'latency_ms':timing_summary(results),'thresholds':thresholds,'rejected':rejected,
            'demonstrations':real_demos(observations), 'reference_sessions':sorted(reference_sessions)}


def real_demos(observations):
    groups={'moved_product':[], 'fitness_bar_not_milk':[], 'remove_one_of_two':[], 'hand':[]}
    moved_positions={}
    for row in observations:
        scenario=str(row['scenario'] or '')
        result=row['result']
        if 'move' in scenario or 'swap' in scenario:
            for obj in row['truth_objects']:
                identity=obj['identity']
                if identity in IDENTITIES:
                    moved_positions.setdefault(identity,set()).add(tuple(obj['box'][:2]))
                    x,y,w,h=obj['box']
                    candidates=[o for o in result['objects'] if x <= o['box'][0]+o['box'][2]/2 <= x+w and y <= o['box'][1]+o['box'][3]/2 <= y+h]
                    groups['moved_product'].append(bool(candidates) and all(o['identity']==identity for o in candidates))
        for obj in row['truth_objects']:
            if obj['identity']=='exp:fitness-bar':
                x,y,w,h=obj['box']
                candidates=[o for o in result['objects'] if x <= o['box'][0]+o['box'][2]/2 <= x+w and y <= o['box'][1]+o['box'][3]/2 <= y+h]
                groups['fitness_bar_not_milk'].append(bool(candidates) and all(o['identity']!='exp:milk' for o in candidates))
        if row.get('expected_obstructed'):
            groups['hand'].append(result['uncertain'] or any(o['status']=='obstructed' for o in result['objects']))
        if row['expected_counts'] is not None:
            groups['remove_one_of_two'].append((row['expected_counts'],result['visible_units'],row['sample'].split('/')[0]))
    count_pairs=groups.pop('remove_one_of_two')
    count_evidence=[]
    for i,(expected,predicted,recording) in enumerate(count_pairs):
        for later_expected,later_predicted,later_recording in count_pairs[i+1:]:
            if recording==later_recording:
                for identity in IDENTITIES:
                    if expected.get(identity)==2 and later_expected.get(identity)==1:
                        count_evidence.append(predicted.get(identity)==2 and later_predicted.get(identity)==1)
    if not any(len(positions)>=2 for positions in moved_positions.values()):
        groups['moved_product']=[]
    groups['remove_one_of_two']=count_evidence
    return {key:{'state':'measured' if values else 'idle: no validated evidence', 'checks':len(values),
                 'passed':sum(values)} for key,values in groups.items()}


def print_tables(report):
    print(f"\n{report['experiment_label'].upper()} — {report.get('note',report.get('reason',''))}")
    for key in ['identity_per_crop','visible_units_per_frame','demonstrations','latency_ms']:
        print('\n'+key)
        print('| Metric | Value |\n|---|---|')
        for metric,value in report.get(key,{}).items():
            print(f'| {metric} | {json.dumps(value)} |')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--synthetic',action='store_true')
    parser.add_argument('--store',type=Path,default=ROOT/'store')
    parser.add_argument('--recordings',type=Path,default=INTEGRATION/'data/recordings/evaluation')
    parser.add_argument('--steady',type=Path,default=ROOT.parents[2]/'tests/device/sequences/steady-01/frames/000003.jpg')
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    if args.synthetic:
        # A fresh isolated store prevents generated proxy labels entering the real gallery.
        from .storage import identifier
        store=args.store/'synthetic'/identifier()
        embed=DinoEmbedder(args.store/'model_cache')
        report=synthetic(ShelfService(Store(store,identities=IDENTITIES),embed),args.steady)
    else:
        report=evaluate_real(ShelfService(Store(args.store,identities=IDENTITIES)),args.recordings)
    output=args.output or args.store/('synthetic-report.json' if args.synthetic else 'real-report.json')
    write_json(output,report)
    print_tables(report)


if __name__=='__main__':
    main()
