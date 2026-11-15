import base64
import io
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from shelf_service.service import ShelfService
from shelf_service.storage import ARCHIVED_IDENTITIES, Store


class FakeEmbedder:
    loaded = True
    info = {'name': 'test-colours', 'revision': 'test-only', 'device': 'cpu'}

    def load(self):
        pass

    def __call__(self, image):
        # Deterministic, visibly distinct from the production DINO model.
        rgb = np.asarray(image).reshape(-1,3).mean(axis=0)/255
        return np.array([*rgb, .15], dtype=np.float32)


@pytest.fixture
def service(tmp_path):
    # The pipeline tests use the archived props identities (their history); the live set is tested in test_vision.
    return ShelfService(Store(tmp_path/'store', identities=ARCHIVED_IDENTITIES), FakeEmbedder())


def scene(boxes=(), background=(20,20,20), size=(300,200)):
    image = Image.new('RGB', size, background)
    draw = ImageDraw.Draw(image)
    for box, colour in boxes:
        draw.rectangle(box, fill=colour)
    return image


def body(image, **extra):
    buf = io.BytesIO()
    image.save(buf, format='JPEG', quality=100, subsampling=0)
    return {'image_b64': base64.b64encode(buf.getvalue()).decode(), 'surface':[0,0,1,1],
            'captured_at':'2026-11-14T12:00:00+02:00', **extra}


def seed(service):
    service.set_background(scene(), [0,0,1,1], '2026-11-14T10:00:00Z')
    for identity, colour in [('exp:milk',(240,20,20)), ('exp:chips',(20,240,20))]:
        for offset in (0,2):
            im = scene([((60+offset,55,95+offset,110+offset), colour)])
            service.enrol(identity, im, [0,0,1,1], 'reference', '2026-11-14T10:00:01Z', 'ref')
