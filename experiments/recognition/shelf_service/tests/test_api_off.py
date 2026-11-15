import json
import httpx
import pytest
from fastapi.testclient import TestClient

from shelf_service.app import create_app
from shelf_service.off import OffClient, FIELDS
from conftest import body, scene


def test_api_contract(service):
    with TestClient(create_app(service, preload=False)) as client:
        assert client.get('/health').json()['model_loaded'] is True
        assert client.post('/background',json=body(scene())).json() == {'ok':True}
        response = client.post('/recognize/embedding',json=body(scene([((100,50,140,110),'red')])) )
        assert response.status_code == 200
        data = response.json()
        assert {'captured_at','surface','objects','visible_units','uncertain','reasons','timing_ms','model','gallery','note','thresholds'} <= data.keys()
        assert {'box','status','identity','label','candidates','area_px'} <= data['objects'][0].keys()
        assert set(data['timing_ms']) == {'detect','embed','match','total'}
        assert data['objects'][0]['identity'] is None
        assert client.post('/enrol/photo',json=body(scene([((100,50,140,110),'red')]),identity='exp:milk',role='reference')).status_code == 200
        enrolled = client.get('/enrolment').json()['exp:milk']
        assert enrolled['reference'] == 1 and enrolled['heldout'] == 0
        assert 'catalogue_match' in enrolled and 'unit_pack_note' in enrolled


@pytest.mark.parametrize('change', [{'surface':[0,0,2,1]}, {'surface':[0,0,0,1]}, {'image_b64':'broken'}, {'captured_at':'2026-11-14T12:00:00'}])
def test_invalid_input_422(service, change):
    with TestClient(create_app(service, preload=False)) as client:
        assert client.post('/recognize/embedding',json={**body(scene()),**change}).status_code == 422


def test_fitness_bar_rejected_by_api(service):
    with TestClient(create_app(service, preload=False)) as client:
        assert client.post('/enrol/photo',json=body(scene(),identity='exp:fitness-bar',role='reference')).status_code == 422


@pytest.mark.parametrize('status,data', [(200,{'status':1,'product':{'product_name':'test','quantity':'1 l'}}),
                                       (200,{'status':0}), (404,{'status':'failure'}), (503,{'error':'down'})])
def test_off_response_and_failure_cache(tmp_path,status,data):
    requests = []
    def handler(request):
        requests.append(request)
        assert request.method == 'GET'
        assert request.url.params['fields'] == FIELDS
        assert request.headers['User-Agent'].startswith('ValioShelfPrototype/0.1')
        return httpx.Response(status,json=data)
    off = OffClient(tmp_path, httpx.Client(transport=httpx.MockTransport(handler)))
    first = off.lookup('12345678')
    second = off.lookup('12345678')
    assert len(requests) == 1 and second['cached']
    assert first['found'] == (data.get('status') == 1)
    assert first['licences']['database'] == 'ODbL 1.0'
    assert first['licences']['images'] == 'CC BY-SA 3.0'
    assert 'retrieved_at' in first


def test_off_rate_limit_persists_across_restart(tmp_path):
    calls = []
    client = httpx.Client(transport=httpx.MockTransport(lambda r: calls.append(r) or httpx.Response(200,json={'status':0})))
    off = OffClient(tmp_path,client,clock=lambda:100)
    for i in range(10):
        off.lookup(str(12345670+i))
    restarted = OffClient(tmp_path,client,clock=lambda:101)
    assert restarted.lookup('98765432')['retry_after'] == 60
    assert len(calls) == 10
    restarted.clock = lambda:161
    restarted.lookup('98765432')
    assert len(calls) == 11


def test_off_network_failure_manual_enrol_unaffected(service,tmp_path):
    def handler(request):
        raise httpx.ConnectError('offline')
    service.off = OffClient(tmp_path,httpx.Client(transport=httpx.MockTransport(handler)))
    with TestClient(create_app(service,preload=False)) as client:
        assert client.post('/enrol/off/12345678').json()['error'] == 'OFF network failure'
        client.post('/background',json=body(scene()))
        assert client.post('/enrol/photo',json=body(scene([((60,55,95,110),'red')]),identity='exp:milk',role='reference')).status_code == 200


def test_off_image_cache_and_host_restriction(tmp_path):
    import base64
    photo = base64.b64decode(body(scene())['image_b64'])
    calls=[]
    def handler(request):
        calls.append(request)
        if request.url.host == 'images.openfoodfacts.org':
            return httpx.Response(200,content=photo)
        return httpx.Response(200,json={'status':'success','product':{'image_front_url':'https://images.openfoodfacts.org/test.jpg'}})
    off=OffClient(tmp_path,httpx.Client(transport=httpx.MockTransport(handler)))
    result=off.lookup('12345678')
    assert result['source']=='open-food-facts' and (tmp_path/result['image_path']).exists()
    off.lookup('12345678')
    assert len(calls)==2


def test_off_no_implicit_identity_mapping(service,tmp_path):
    service.off=OffClient(tmp_path,httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'status':1,'product':{'product_name':'milk'}}))))
    with TestClient(create_app(service,preload=False)) as client:
        client.post('/enrol/off/12345678')
        assert not client.get('/enrolment').json()['exp:milk']['off_data_present']
        client.post('/enrol/off/12345678',json={'identity':'exp:milk'})
        assert client.get('/enrolment').json()['exp:milk']['off_data_present']
        assert client.get('/health').json()['gallery']['exp:milk']==0


def test_off_untrusted_image_host_is_not_fetched(tmp_path):
    calls=[]
    def handler(request):
        calls.append(request)
        return httpx.Response(200,json={'status':1,'product':{'image_front_url':'http://127.0.0.1:8000/orders'}})
    result=OffClient(tmp_path,httpx.Client(transport=httpx.MockTransport(handler))).lookup('12345678')
    assert len(calls)==1 and result['image_path'] is None
    assert result['image_error']=='image URL is not the OFF image host'


def test_off_gallery_opt_in_and_disable(service):
    from conftest import seed
    seed(service)
    directory=service.store.root/'off_cache'
    directory.mkdir(exist_ok=True)
    scene(background='blue').save(directory/'12345678.jpg')
    meta=service.store.meta('exp:milk')
    meta['off']={'source':'open-food-facts','source_url':'https://world.openfoodfacts.org/product/12345678',
                 'barcode':'12345678','image_path':'12345678.jpg','use_image':True,'found':True}
    service.store.save_meta('exp:milk',meta)
    bg,bgm=service.store.background()
    assert len(service.gallery(bg,bgm)[1])==4
    service.use_off_images=True
    vectors,labels,thresholds=service.gallery(bg,bgm)
    assert len(labels)==5 and thresholds['reference_count']==4
    assert thresholds['external_references'][0]['source']=='open-food-facts'
    service.use_off_images=False
    assert len(service.gallery(bg,bgm)[1])==4


def test_region_endpoint_validates_and_allows_only_the_board_page(service):
    with TestClient(create_app(service, preload=False)) as client:
        bad = client.post('/recognize/region', json={**body(scene()), 'region': [0.9, 0.9, 0.2, 0.2]})
        assert bad.status_code == 422
        pre = lambda origin: client.options('/recognize/region', headers={
            'Origin': origin, 'Access-Control-Request-Method': 'POST', 'Access-Control-Request-Headers': 'content-type'})
        assert pre('http://172.20.10.2:7000').headers.get('access-control-allow-origin') == 'http://172.20.10.2:7000'
        assert 'access-control-allow-origin' not in pre('http://example.com').headers


def test_the_service_reads_the_shared_product_set_and_accepts_partial_model_coverage(service, tmp_path, monkeypatch):
    from shelf_service.tests.sets import ACTIVE
    import base64, io
    from PIL import Image
    shared = {"schema": "shelf-product-set/2", "set_id": ACTIVE.set_id, "routing": ACTIVE.routing,
              "families": ACTIVE.families, "products": ACTIVE.products, "excluded_products": ACTIVE.excluded}
    (tmp_path / "active.json").write_text(json.dumps(shared))
    monkeypatch.setenv("SHELF_PRODUCTS", str(tmp_path / "active.json"))
    b = io.BytesIO(); Image.new("RGB", (480, 360), (50, 50, 50)).save(b, "JPEG")
    obs = {"image_b64": base64.b64encode(b.getvalue()).decode(), "captured_at": "2026-11-15T03:00:00+02:00",
           "frame_seq": 1, "session": "s1", "detections": [{"box": [0.1, 0.1, 0.2, 0.3], "score": 0.97,
                                                           "identity": "aimo:aito-barista-oat"}]}
    with TestClient(create_app(service, preload=False)) as client:
        h = client.get('/health').json()['product_set']
        assert h['set_id'] == 'kitchen-20261115-b' and len(h['products']) == 4
        assert client.get('/products').json()['order_units']['aimo:bonne-orange-juice']['count_units_per_sales_unit'] == 18
        from shelf_service.tests.sets import FIVE_CLASS
        # a model without a class order (no trainer metadata) is accepted: its numbered boxes go to the cloud check
        unmapped = {**obs, "detections": [{"box": [0.1, 0.1, 0.2, 0.3], "score": 0.97, "class_id": 7}]}
        old = client.post('/reconcile', json={**unmapped, "model": {"name": "v3", "classes": None, "num_classes": 10}})
        assert old.status_code == 200 and old.json()['objects'][0]['route'] == 'cloud'
        assert old.json()['coverage']['class_map'] == 'awaiting trainer metadata'
        ok = client.post('/reconcile', json={**obs, "frame_seq": 2, "model": {"product_set": "kitchen-20261115-a", "classes": FIVE_CLASS}})
        assert ok.status_code == 200 and ok.json()['counts']['aimo:aito-barista-oat']['counted'] == 1
        assert client.post('/reconcile', json={**obs, "frame_seq": 2, "model": {"classes": FIVE_CLASS}}).status_code == 409  # same frame again
    monkeypatch.setenv("SHELF_PRODUCTS", str(tmp_path / "missing.json"))
    with TestClient(create_app(service, preload=False)) as client:
        assert client.post('/reconcile', json={**obs, "model": {"classes": FIVE_CLASS}}).status_code == 503
        assert 'error' in client.get('/health').json()['product_set']


def test_enrolment_choices_follow_the_active_set(tmp_path, monkeypatch):
    from shelf_service.service import ShelfService
    from shelf_service.storage import Store
    from shelf_service.tests.sets import ACTIVE
    from conftest import FakeEmbedder
    (tmp_path / "active.json").write_text(json.dumps({"schema": "shelf-product-set/2", "set_id": ACTIVE.set_id,
                                                      "routing": ACTIVE.routing, "families": ACTIVE.families,
                                                      "products": ACTIVE.products, "excluded_products": ACTIVE.excluded}))
    svc = ShelfService(Store(tmp_path / "store", str(tmp_path / "active.json")), FakeEmbedder())
    with TestClient(create_app(svc, preload=False)) as client:
        rows = client.get('/enrolment').json()
        assert sorted(rows) == sorted(ACTIVE.identities) and rows['aimo:bonne-orange-juice']['label'] == 'Bonne orange juice (18-pack)'
        assert client.post('/enrol/photo', json=body(scene(), identity='exp:milk', role='reference')).status_code == 422
        assert client.post('/enrol/photo', json=body(scene(), identity='aimo:profeel-salted-caramel', role='reference')).status_code == 422
