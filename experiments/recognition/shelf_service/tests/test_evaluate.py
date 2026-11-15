import json
from pathlib import Path

import pytest

from shelf_service.evaluate import evaluate_real, validated_recordings, recording_frames, real_demos
from shelf_service.storage import write_json
from conftest import scene, seed


def human_labels(**extra):
    return {'validated':True,'validated_by':'test-human','validation_source':'human',
            'role':'evaluation','session_id':'eval','frames':[{'file':'frame.jpg','objects':[]}],**extra}


@pytest.mark.parametrize('change', [{'validated':False},{'validation_source':'model'},{'validated_by':''},{'session_id':'ref'},{'role':'reference'}])
def test_reject_unvalidated_and_reference_sessions(tmp_path,change):
    write_json(tmp_path/'clip/labels.json',human_labels(**change))
    accepted,rejected=validated_recordings(tmp_path,{'ref'})
    assert not accepted and len(rejected)==1


def test_real_idle_when_no_reference(service,tmp_path):
    report=evaluate_real(service,tmp_path/'recordings')
    assert report['state']=='idle'
    assert report['identity_per_crop']['n']==0
    assert report['visible_units_per_frame']['n']==0


def test_real_heldout_and_frame_tables_separate(service,tmp_path):
    seed(service)
    service.enrol('exp:milk',scene([((160,65,195,125),(240,20,20))]),[0,0,1,1],'heldout','now','eval')
    directory=tmp_path/'evaluation/clip'
    directory.mkdir(parents=True)
    scene([((140,70,175,125),(240,20,20))]).save(directory/'frame.jpg',quality=100,subsampling=0)
    write_json(directory/'labels.json',human_labels(frames=[{'file':'frame.jpg','objects':[{'box':[140/300,70/200,36/300,56/200],'identity':'exp:milk'}], 'visible_units':{'exp:milk':1}}]))
    report=evaluate_real(service,tmp_path/'evaluation')
    assert report['identity_per_crop']['n']==2
    assert report['visible_units_per_frame']['n']==1
    assert report['identity_per_crop']['correct']==2
    assert report['visible_units_per_frame']['exact']==1


def test_legacy_windows_use_actual_identity_not_assigned_slot(tmp_path):
    write_json(tmp_path/'config.json',{'slots':[{'slot':1,'rect':[0,0,.5,1]},{'slot':2,'rect':[.5,0,.5,1]}]})
    (tmp_path/'frames').mkdir()
    scene().save(tmp_path/'frames/1.jpg')
    (tmp_path/'meta.jsonl').write_text(json.dumps({'kind':'frame','file':'1.jpg','t':1})+'\n')
    labels=human_labels(frames=[],labels=[{'from_t':0,'to_t':2,'slots':{'1':'exp:milk','2':'unknown'},
                        'counts':{'1':1,'2':1},'identities':{'1':'exp:milk','2':'exp:fitness-bar'}}])
    frames=list(recording_frames(tmp_path,labels))
    assert frames[0]['objects'][1]['identity']=='exp:fitness-bar'
    assert frames[0]['expected_counts']['exp:milk']==1
    assert frames[0]['expected_counts']['exp:chips']==0


def test_legacy_counts_not_inferred_from_presence(tmp_path):
    write_json(tmp_path/'config.json',{'slots':[{'slot':1,'rect':[0,0,1,1]}]})
    (tmp_path/'frames').mkdir()
    scene().save(tmp_path/'frames/1.jpg')
    (tmp_path/'meta.jsonl').write_text(json.dumps({'kind':'frame','file':'1.jpg','t':1})+'\n')
    labels=human_labels(frames=[],labels=[{'from_t':0,'to_t':2,'slots':{'1':'exp:milk'}}])
    assert list(recording_frames(tmp_path,labels))==[]


def test_reject_reference_frame_duplicate(service,tmp_path):
    seed(service)
    reference=service.store.references()[0][1]
    directory=tmp_path/'eval/clip'
    directory.mkdir(parents=True)
    (directory/'frame.jpg').write_bytes((service.store.root/reference['frame_path']).read_bytes())
    write_json(directory/'labels.json',human_labels())
    report=evaluate_real(service,tmp_path/'eval')
    assert report['rejected'][0]['reason']=='frame duplicates a reference'
    assert report['visible_units_per_frame']['n']==0


def test_native_frame_path_cannot_escape(tmp_path):
    with pytest.raises(ValueError):
        list(recording_frames(tmp_path,human_labels(frames=[{'file':'../outside.jpg'}])))


def test_demo_requires_actual_move_and_count_transition():
    assert all(v['checks']==0 for v in real_demos([]).values())


def test_invalid_recording_cannot_leave_partial_scores(service,tmp_path):
    seed(service)
    directory=tmp_path/'eval/clip'
    directory.mkdir(parents=True)
    scene([((140,70,175,125),(240,20,20))]).save(directory/'frame.jpg',quality=100,subsampling=0)
    write_json(directory/'labels.json',human_labels(frames=[
        {'file':'frame.jpg','objects':[{'box':[.4,.3,.15,.3],'identity':'exp:milk'}]},
        {'file':'frame.jpg','objects':[{'box':[0,0,2,2],'identity':'exp:milk'}]}]))
    report=evaluate_real(service,tmp_path/'eval')
    assert report['identity_per_crop']['n']==0
    assert report['rejected'][0]['reason']=='invalid normalised object box'
