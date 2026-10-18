"""Create-only GCS envelope behavior against the exact approved reference replay."""
from copy import deepcopy
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from procure.demo.market_snapshot_store import GcsMarketStore, TRUSTED_PACKAGE_SOURCE_SHA256


IMAGE = 'sha256:' + 'a' * 64
REFERENCE = json.loads((Path(__file__).parents[1] / 'config' /
                        'market_reference_snapshot_v1.json').read_text())


def reference_snapshot(run='reference-r1'):
    snapshot = deepcopy(REFERENCE)
    snapshot.update(run_id=run, job_image_digest=IMAGE,
                    publication_mode='REFERENCE_REPLAY',
                    source_code_sha256=TRUSTED_PACKAGE_SOURCE_SHA256)
    for cell in snapshot['forecasts']:
        cell['run_id'] = run
    return snapshot


class MemoryStore(GcsMarketStore):
    def __init__(self):
        super().__init__('test-bucket')
        self.objects = {}
        self.next_generation = 1

    def metadata(self, key):
        if key not in self.objects:
            raise HTTPError(key, 404, 'missing', {}, None)
        return {'generation': self.objects[key][0]}

    def get(self, key, generation=None):
        current, content = self.objects[key]
        if generation is not None and str(current) != str(generation):
            raise HTTPError(key, 404, 'generation missing', {}, None)
        return content

    def create(self, key, content, *, generation_match='0'):
        current = self.objects.get(key)
        if (generation_match == '0' and current is not None) or (
            generation_match != '0' and (current is None or current[0] != generation_match)
        ):
            raise HTTPError(key, 412, 'precondition', {}, None)
        generation = str(self.next_generation)
        self.next_generation += 1
        self.objects[key] = (generation, content)
        return generation


class StoreTests(unittest.TestCase):
    def test_create_only_archive_and_pointer_retry(self):
        store = MemoryStore()
        snapshot = reference_snapshot()
        with patch.dict(os.environ, {'ZOORQ_JOB_IMAGE_DIGEST': IMAGE}):
            pointer = store.publish(snapshot)
            self.assertEqual(store.read_latest()[0]['run_id'], 'reference-r1')
            self.assertEqual(store.publish(snapshot), pointer)
            self.assertEqual(len(store.objects), 3)
            changed = reference_snapshot('reference-other')
            changed['as_of_utc'] = '2099-01-01T00:00:00Z'
            with self.assertRaises(ValueError):
                store.publish(changed)
            self.assertEqual(store.read_latest()[0]['run_id'], 'reference-r1')

    def test_reader_requires_exact_approved_image(self):
        store = MemoryStore()
        with patch.dict(os.environ, {'ZOORQ_JOB_IMAGE_DIGEST': IMAGE}):
            store.publish(reference_snapshot())
        with patch.dict(os.environ, {'ZOORQ_JOB_IMAGE_DIGEST': '',
                                     'ZOORQ_PREDICTION_JOB_IMAGE_DIGEST': ''}):
            with self.assertRaises(ValueError):
                store.read_latest()
        with patch.dict(os.environ, {'ZOORQ_JOB_IMAGE_DIGEST': '',
                                     'ZOORQ_PREDICTION_JOB_IMAGE_DIGEST': 'sha256:' + 'b' * 64}):
            with self.assertRaises(ValueError):
                store.read_latest()


if __name__ == '__main__':
    unittest.main()
