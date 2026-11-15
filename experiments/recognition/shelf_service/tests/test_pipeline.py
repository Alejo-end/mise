import json

import numpy as np
import pytest

from shelf_service.calibration import calibrate, classify
from shelf_service.detection import DetectionConfig, detect
from conftest import scene, seed


@pytest.mark.parametrize('surface', [[0,0,1,1], [.2,.2,.6,.6]])
def test_empty(surface):
    assert detect(scene(), scene(), surface)['objects'] == []


def test_separated_and_full_frame_coordinates():
    image = scene([((80,60,105,110),'red'), ((160,70,190,120),'green'), ((0,0,20,20),'white')])
    out = detect(image, scene(), [.2,.2,.6,.6])
    assert len(out['objects']) == 2
    for o in out['objects']:
        assert .2 <= o['box'][0] < .8 and .2 <= o['box'][1] < .8
        assert o['area_px'] > 500


@pytest.mark.parametrize('side,box', [('top',(100,0,140,100)), ('bottom',(100,100,140,199)),
                                     ('left',(0,60,100,100)), ('right',(200,60,299,100))])
def test_entry_border_obstruction(side, box):
    out = detect(scene([(box,'white')]), scene(), [0,0,1,1], DetectionConfig(entry_side=side))
    assert out['objects'][0]['status'] == 'obstructed'


def test_large_object_and_lighting():
    out = detect(scene([((20,20,280,180),'white')]), scene(), [0,0,1,1])
    assert out['objects'][0]['status'] == 'obstructed'
    assert detect(scene(background='white'), scene(), [0,0,1,1])['reasons']


def test_touching_packages_ambiguous():
    out = detect(scene([((60,60,140,120),'red')]), scene(), [0,0,1,1], package_area=.03)
    assert out['objects'][0]['status'] == 'ambiguous'


def test_reference_only_calibration_and_decisions():
    vectors = [[1,0,0], [.99,.01,0], [0,1,0], [.01,.99,0]]
    labels = ['a','a','b','b']
    thresholds = calibrate(vectors, labels, [[0,0,1]])
    assert thresholds['ready'] and not thresholds['heldout_used']
    assert classify([1,0,0], vectors, labels, thresholds)['identity'] == 'a'
    assert classify([0,0,1], vectors, labels, thresholds)['status'] == 'unknown'
    assert classify([1,1,0], vectors, labels, {**thresholds,'accept':.5,'margin':.1})['status'] == 'ambiguous'
    assert not calibrate([[1,0]], ['a'], [[0,1]])['ready']


def test_move_count_remove_obstruct_and_unknown(service):
    seed(service)
    for x in (35,180):
        result = service.recognize(scene([((x,60,x+35,115),(240,20,20))]), [0,0,1,1], 'now')
        assert result['objects'][0]['identity'] == 'exp:milk'
    result = service.recognize(scene([((35,60,70,115),(240,20,20)), ((180,60,215,115),(240,20,20))]), [0,0,1,1], 'now')
    assert result['visible_units']['exp:milk'] == 2
    result = service.recognize(scene([((180,60,215,115),(240,20,20))]), [0,0,1,1], 'now')
    assert result['visible_units']['exp:milk'] == 1
    result = service.recognize(scene([((100,0,150,100),'white')]), [0,0,1,1], 'now')
    assert result['uncertain'] and result['objects'][0]['status'] == 'obstructed'
    result = service.recognize(scene([((100,60,135,115),'blue')]), [0,0,1,1], 'now')
    assert result['objects'][0]['status'] == 'unknown'
    assert len(list((service.store.root/'runs').glob('*.json'))) == 6


def test_heldout_cannot_change_gallery_thresholds_or_area(service):
    seed(service)
    frame = scene([((150,60,185,115),(240,20,20))])
    before = service.recognize(frame, [0,0,1,1], 'now')
    service.enrol('exp:milk', scene([((140,70,180,135),'blue')]), [0,0,1,1], 'heldout', 'now', 'eval')
    service._gallery_key = None
    after = service.recognize(frame, [0,0,1,1], 'now')
    assert before['thresholds'] == after['thresholds']
    assert before['objects'] == after['objects']
    assert after['gallery']['exp:milk'] == 2


@pytest.mark.parametrize('boxes', [[], [((60,60,100,100),'red'),((180,60,220,100),'blue')], [((90,0,140,100),'white')]])
def test_enrol_refuses_empty_multiple_obstructed(service, boxes):
    service.set_background(scene(), [0,0,1,1], 'now')
    with pytest.raises(ValueError):
        service.enrol('exp:milk', scene(boxes), [0,0,1,1], 'reference', 'now')


def test_no_fitness_enrolment_duplicate_or_session_leak(service):
    seed(service)
    for identity, role, session, frame in [
        ('exp:fitness-bar','reference',None,scene()),
        ('exp:milk','heldout','eval',scene([((60,55,95,110),(240,20,20))])),
        ('exp:milk','heldout','ref',scene([((150,60,185,115),'blue')]))]:
        with pytest.raises(ValueError):
            service.enrol(identity,frame,[0,0,1,1],role,'now',session)


def test_missing_background_and_changed_surface_uncertain(service):
    assert service.recognize(scene(),[0,0,1,1],'now')['uncertain']
    seed(service)
    assert service.recognize(scene(),[.1,0,.9,1],'now')['uncertain']
    assert service.recognize(scene(size=(600,400)),[0,0,1,1],'now')['uncertain']


def test_model_failure_is_uncertain_and_persisted(service):
    seed(service)
    def fail(image):
        raise RuntimeError('test')
    service.embed.__class__.__call__, previous = fail, service.embed.__class__.__call__
    try:
        result = service.recognize(scene([((60,55,95,110),'red')]),[0,0,1,1],'now')
        assert result['uncertain'] and sum(result['visible_units'].values()) == 0
        assert len(result['objects']) == 1 and result['objects'][0]['identity'] is None
        assert (service.store.root/'runs'/f"{result['run_id']}.json").exists()
    finally:
        service.embed.__class__.__call__ = previous


def test_enrol_rejects_known_oversize_touching_component(service):
    seed(service)
    with pytest.raises(ValueError,match='ambiguous'):
        service.enrol('exp:milk',scene([((80,60,170,140),'red')]),[0,0,1,1],'reference','now')


def test_lighting_has_no_known_counts(service):
    seed(service)
    result=service.recognize(scene(background='white'),[0,0,1,1],'now')
    assert result['uncertain'] and sum(result['visible_units'].values())==0
    assert all(o['status']=='obstructed' for o in result['objects'])


def test_reference_cache_survives_service_restart(service):
    from shelf_service.service import ShelfService
    seed(service)
    frame=scene([((140,65,175,120),(240,20,20))])
    before=service.recognize(frame,[0,0,1,1],'now')
    restarted=ShelfService(service.store,service.embed)
    after=restarted.recognize(frame,[0,0,1,1],'now')
    assert before['thresholds']==after['thresholds']
    assert before['objects']==after['objects']


def test_new_identity_can_be_larger_than_existing_package(service):
    seed(service)
    # New products have no own size baseline. Existing small packages must not prevent enrolment.
    result=service.enrol('exp:energy-drink',scene([((80,60,170,140),'blue')]),[0,0,1,1],'reference','now')
    assert result['ok']
