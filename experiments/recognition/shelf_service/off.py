"""Read-only OFF v3.6 lookup, durable cache including failures and rate history."""
import io
import os
import re
import threading
import time
from urllib.parse import urlparse

import httpx
from PIL import Image

from .storage import now, write_json

FIELDS = 'product_name,brands,quantity,image_front_url'
DOCS = 'https://openfoodfacts.github.io/openfoodfacts-server/api/'
LICENCES = {'database': 'ODbL 1.0', 'contents': 'Database Contents License', 'images': 'CC BY-SA 3.0',
            'source': DOCS, 'terms': 'https://world.openfoodfacts.org/terms-of-use'}


class OffClient:
    def __init__(self, root, client=None, clock=time.time):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.client = client or httpx.Client(timeout=15, follow_redirects=False, trust_env=False)
        self.clock = clock
        self.lock = threading.Lock()

    def reserve(self):
        import json
        path = self.root/'requests.json'
        previous = json.loads(path.read_text()) if path.exists() else []
        current = self.clock()
        recent = [t for t in previous if current-t < 60]
        if len(recent) >= 10:
            return False
        write_json(path, recent + [current])
        return True

    def lookup(self, barcode):
        import json
        if not re.fullmatch(r'\d{8,14}', barcode):
            raise ValueError('barcode must be 8 to 14 digits')
        with self.lock:
            path = self.root/f'{barcode}.json'
            if path.exists():
                return {**json.loads(path.read_text()), 'cached': True}
            source = f'https://world.openfoodfacts.org/api/v3.6/product/{barcode}.json'
            result = {'barcode': barcode, 'source': 'open-food-facts', 'source_url': source,
                      'retrieved_at': now(), 'licences': LICENCES, 'found': False,
                      'product': {}, 'cached': False, 'image_path': None}
            if not self.reserve():
                # No remote request happened; do not poison the product cache.
                return {**result, 'error': 'local rate limit (10 requests/minute)', 'retry_after': 60}
            ua = 'ValioShelfPrototype/0.1 (Junction 2026 hackathon prototype)'
            contact = os.environ.get('OFF_CONTACT', '').strip()
            if contact:
                ua += ' (' + contact.replace('\r', '').replace('\n', '') + ')'
            headers = {'User-Agent': ua}
            try:
                response = self.client.get(source, params={'fields': FIELDS}, headers=headers)
                result['http_status'] = response.status_code
                # Cache response bodies for traceability, not headers/request objects/contact.
                try:
                    result['response'] = response.json()
                except ValueError:
                    result['response'] = {'non_json': True}
                if response.status_code == 200 and isinstance(result['response'], dict):
                    data = result['response']
                    product = data.get('product')
                    result['found'] = isinstance(product, dict) and bool(product) and data.get('status') not in (0, 'failure')
                    result['product'] = {k: product[k] for k in FIELDS.split(',') if k in product} if result['found'] else {}
                    if not result['found']:
                        result['error'] = 'product missing'
                else:
                    result['error'] = f'OFF HTTP {response.status_code}'
                if response.status_code == 429:
                    result['retry_after'] = response.headers.get('Retry-After', '60')
            except httpx.HTTPError:
                result['error'] = 'OFF network failure'
            write_json(path, result)  # product response survives optional image failure
            image_url = result['product'].get('image_front_url')
            if image_url:
                parsed = urlparse(image_url)
                if parsed.scheme != 'https' or parsed.hostname != 'images.openfoodfacts.org':
                    result['image_error'] = 'image URL is not the OFF image host'
                elif not self.reserve():
                    result['image_error'] = 'local rate limit; metadata remains available'
                else:
                    try:
                        response = self.client.get(image_url, headers=headers)
                        result['image_response'] = {'status': response.status_code, 'source_url': image_url, 'retrieved_at': now()}
                        response.raise_for_status()
                        if len(response.content) > 12_000_000:
                            raise ValueError('image too large')
                        image = Image.open(io.BytesIO(response.content))
                        if image.width * image.height > 16_000_000:
                            raise ValueError('image too large')
                        image_path = self.root/f'{barcode}.jpg'
                        image.convert('RGB').save(image_path, quality=95)
                        result['image_path'] = image_path.name
                    except (httpx.HTTPError, ValueError, OSError, Image.DecompressionBombError):
                        result['image_error'] = 'image unavailable or invalid'
            write_json(path, result)
            return result
