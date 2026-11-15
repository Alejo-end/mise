"""Run from experiments/recognition with the sibling service dependency project."""
import os
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, field_validator

from .detection import DetectionConfig
from .service import ShelfService
from . import product_set as PS
from .storage import Store, decode_image


class Photo(BaseModel):
    image_b64: str = Field(max_length=16_000_000)
    surface: tuple[float, float, float, float]
    captured_at: datetime

    @field_validator('surface')
    @classmethod
    def valid_surface(cls, value):
        import math
        x,y,w,h = value
        if not all(math.isfinite(v) for v in value) or x < 0 or y < 0 or w <= 0 or h <= 0 or x+w > 1 or y+h > 1:
            raise ValueError('surface must be a nonempty normalised rectangle inside the frame')
        return value

    @field_validator('captured_at')
    @classmethod
    def timezone_required(cls, value):
        if value.tzinfo is None:
            raise ValueError('captured_at must include a timezone')
        return value


class RegionPhoto(Photo):
    """One region selected on the page (normally a local-model box), checked with the whole frame as context."""
    region: tuple[float, float, float, float]
    prompted_by: dict | None = None  # what the page showed for that region (echoed into the result, never sent to the model)

    @field_validator('region')
    @classmethod
    def valid_region(cls, value):
        return Photo.valid_surface(value)

    @field_validator('prompted_by')
    @classmethod
    def small_context(cls, value):
        if value is not None and len(str(value)) > 2000:
            raise ValueError('prompted_by is too large')
        return value


class EnrolPhoto(Photo):
    identity: str = Field(max_length=120)  # a product of the active set (checked against the shared file)
    role: Literal['reference', 'heldout']
    session_id: str | None = Field(default=None, max_length=160)


class OffRequest(BaseModel):
    identity: str | None = Field(default=None, max_length=120)
    use_image: bool = False


class Detection(BaseModel):
    box: tuple[float, float, float, float]
    score: float = Field(ge=0, le=1)
    identity: str | None = Field(default=None, max_length=120)
    class_id: int | None = None
    class_scores: dict[str, float] | None = None

    @field_validator('box')
    @classmethod
    def valid_box(cls, value):
        return Photo.valid_surface(value)


class HybridRequest(BaseModel):
    """One frame of the box's hybrid recognition: its local model's detections of exactly this frame and what the
    cloud side should do (the box decides from the scene: whole-frame on a new or changed settled scene, reuse while
    it is unchanged, none when it is not settled)."""
    image_b64: str = Field(max_length=4_000_000)
    captured_at: datetime
    request_id: str | None = Field(default=None, max_length=60)
    frame_id: int | None = None
    local: dict | None = None
    cloud: Literal['whole-frame', 'reuse', 'none'] = 'whole-frame'
    reuse: dict | None = None
    crop_checks: bool = True

    @field_validator('captured_at')
    @classmethod
    def timezone_required(cls, value):
        return Photo.timezone_required(value)

    @field_validator('local')
    @classmethod
    def small_local(cls, value):
        if value is not None and len((value or {}).get('detections') or []) > 200:
            raise ValueError('too many local detections')
        return value


class Observation(BaseModel):
    """One frame of the local model in detection/repeat mode, for the automatic cloud fallback."""
    image_b64: str = Field(max_length=4_000_000)
    captured_at: datetime
    frame_seq: int = Field(ge=0)
    session: str = Field(default='default', max_length=60)
    detections: list[Detection] = Field(max_length=100)
    model: dict

    @field_validator('captured_at')
    @classmethod
    def timezone_required(cls, value):
        return Photo.timezone_required(value)


def _jpeg(b64: str) -> bytes:
    import base64 as _b64
    try:
        jpeg = _b64.b64decode(b64, validate=True)
    except ValueError as exc:
        raise HTTPException(422, 'image_b64 is not base64') from exc
    if not jpeg.startswith(b'\xff\xd8'):
        raise HTTPException(422, 'image is not a JPEG')
    return jpeg


def _active_set():
    try:
        return PS.active()
    except (RuntimeError, ValueError, OSError) as exc:
        raise HTTPException(503, f'active product set unavailable: {exc}') from exc


def create_app(service=None, preload=True):
    service = service or ShelfService(
        store=Store(os.environ.get('SHELF_STORE', str(__import__('pathlib').Path(__file__).parent/'store')),
                    os.environ.get('SHELF_PRODUCTS')),
        config=DetectionConfig(entry_side=os.environ.get('SHELF_ENTRY_SIDE', 'top'),
                               max_area_fraction=float(os.environ.get('SHELF_MAX_AREA', '.45'))),
        use_off_images=os.environ.get('SHELF_USE_OFF_IMAGES', '0') == '1')

    @asynccontextmanager
    async def lifespan(app):
        if preload:
            try:
                service.embed.load()
            except Exception:
                service.model_error = 'model load failed; check local dependency installation and model cache'
        yield

    app = FastAPI(title='Valio observational shelf prototype', lifespan=lifespan)
    app.state.service = service
    # The board page (served by the board app on port 7000) calls /recognize/region directly from the browser.
    from fastapi.middleware.cors import CORSMiddleware
    origins = [o.strip() for o in os.environ.get('SHELF_CORS_ORIGINS', 'http://172.20.10.2:7000').split(',') if o.strip()]
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=['GET', 'POST'], allow_headers=['Content-Type'])

    def execute(photo, operation):
        try:
            image = decode_image(photo.image_b64)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        try:
            with service.lock:
                return operation(image)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get('/health')
    def health():
        with service.lock:
            h = service.health()
        try:
            h['product_set'] = PS.active().summary()
        except (RuntimeError, ValueError, OSError) as exc:
            h['product_set'] = {'error': str(exc)}
        return h

    @app.get('/products')
    def products():
        """The active product set as this service reads it (identities, labels, thresholds, ordering units)."""
        ps = _active_set()
        return ps.summary() | {'families': ps.families, 'routing': ps.routing,
                               'order_units': {p['identity']: (p.get('catalogue') or {}) | {'count_unit': p.get('count_unit')}
                                               for p in ps.products}}

    @app.post('/background')
    def background(photo: Photo):
        return execute(photo, lambda im: service.set_background(im, photo.surface, photo.captured_at.isoformat()))

    @app.post('/recognize')
    async def recognize(photo: Photo):
        """Default since 14 Nov 13:15: the vision model (Kimi K3.1 Pro on Verda), no enrolment or background needed."""
        from pathlib import Path as _P
        from . import vision
        jpeg, ps = _jpeg(photo.image_b64), _active_set()
        try:
            return await vision.recognize(jpeg, photo.surface, photo.captured_at.isoformat(),
                                          _P(__file__).parent / 'store' / 'vision_runs', pset=ps)
        except RuntimeError as exc:
            raise HTTPException(502, str(exc)) from exc

    @app.post('/recognize/region')
    async def recognize_region(photo: RegionPhoto):
        """Manual check of one selected region (vision model), the whole frame given as context. Observational: the
        answer goes back to the page only and is never combined with other counts."""
        from pathlib import Path as _P
        from . import vision
        jpeg, ps = _jpeg(photo.image_b64), _active_set()
        try:
            return await vision.recognize(jpeg, photo.surface, photo.captured_at.isoformat(),
                                          _P(__file__).parent / 'store' / 'vision_runs',
                                          region=list(photo.region), prompted_by=photo.prompted_by, pset=ps)
        except RuntimeError as exc:
            raise HTTPException(502, str(exc)) from exc

    async def ask_object(frame_jpeg, box, captured_at):
        """The selected object only (items around it are context, never counted), for counting."""
        from pathlib import Path as _P
        from . import vision
        r = await vision.recognize(frame_jpeg, [0, 0, 1, 1], captured_at, _P(__file__).parent / 'store' / 'vision_runs',
                                   region=list(box), prompted_by={'automatic': True}, pset=PS.active())
        return vision.resolve_selected(r)

    from .reconcile import Reconciler, Rejected
    reconciler = Reconciler(ask_object)
    app.state.reconciler = reconciler

    @app.post('/recognize/object')
    async def recognize_object(photo: RegionPhoto):
        """One selected object resolved for counting: only items inside the box count, items around it are ignored."""
        jpeg = _jpeg(photo.image_b64)
        _active_set()
        try:
            return await ask_object(jpeg, list(photo.region), photo.captured_at.isoformat())
        except RuntimeError as exc:
            raise HTTPException(502, str(exc)) from exc

    @app.post('/reconcile')
    async def reconcile(obs: Observation):
        """Detection/repeat mode: local detections of one frame, combined with cloud answers for uncertain objects.
        Answers at once; cloud checks run in the background, one at a time. Observational: no stock events, no orders."""
        jpeg = _jpeg(obs.image_b64)
        _active_set()
        try:
            return reconciler.observe(jpeg, obs.captured_at.isoformat(), obs.frame_seq,
                                      [d.model_dump() for d in obs.detections], obs.model, obs.session)
        except Rejected as exc:
            raise HTTPException(exc.code, str(exc)) from exc

    from . import hybrid as _hybrid
    crop_cache = _hybrid.CropCache()
    cloud_gate = __import__('asyncio').Lock()  # one cloud model request of the hybrid flow at a time

    @app.post('/recognize/hybrid')
    async def recognize_hybrid(req: HybridRequest):
        """The box's recognition action: local detections of one frame reconciled with the automatic whole-frame cloud
        check (or its reused answer while the scene is unchanged) into one object list. Observational only."""
        from pathlib import Path as _P
        jpeg, ps = _jpeg(req.image_b64), _active_set()
        r = await _hybrid.run(jpeg, req.captured_at.isoformat(), req.local, req.cloud, req.reuse, ps,
                              _P(__file__).parent / 'store' / 'vision_runs', crops=crop_cache, crop_checks=req.crop_checks,
                              gate=cloud_gate)
        r['request_id'], r['frame_id'] = req.request_id, req.frame_id
        return r

    @app.get('/reconcile/state')
    def reconcile_state():
        return reconciler.state()

    @app.post('/recognize/embedding')
    def recognize_embedding(photo: Photo):
        """The enrolment-based DINOv2 pipeline (needs the empty-surface photo and reference photos)."""
        return execute(photo, lambda im: service.recognize(im, photo.surface, photo.captured_at.isoformat()))

    @app.post('/enrol/photo')
    def enrol(photo: EnrolPhoto):
        if photo.identity not in service.store.identities:
            raise HTTPException(422, f'{photo.identity} is not a product of the active set')
        return execute(photo, lambda im: service.enrol(photo.identity, im, photo.surface, photo.role,
                                                       photo.captured_at.isoformat(), photo.session_id))

    @app.get('/enrolment')
    def enrolment():
        with service.lock:
            return service.store.enrolment()

    @app.post('/enrol/off/{barcode}')
    def off(barcode: str, request: OffRequest | None = None):
        request = request or OffRequest()
        if request.identity and request.identity not in service.store.identities:
            raise HTTPException(422, f'{request.identity} is not a product of the active set')
        try:
            result = service.off.lookup(barcode)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        if request.identity and result.get('found'):
            with service.lock:
                meta = service.store.meta(request.identity)
                meta['off'] = {k: v for k, v in result.items() if k != 'response'}
                meta['off']['use_image'] = request.use_image
                service.store.save_meta(request.identity, meta)
                service._gallery_key = None
        return result

    return app


app = create_app()
