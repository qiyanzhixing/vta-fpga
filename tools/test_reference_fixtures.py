import hashlib,json,pathlib,unittest
ROOT=pathlib.Path(__file__).resolve().parents[1]
class ReferenceTest(unittest.TestCase):
 def test_reference(self):
  self.assertEqual(hashlib.sha256((ROOT/'docs/deNoise_ms.py').read_bytes()).hexdigest(),'0c9934c3ebe9f2f628b834320973705a02994ea08875472c9379a41c195fb139')
  path=ROOT/'internal/denoise/testdata/reference.json'
  self.assertTrue(path.exists(),'reference fixture generator has not run')
  doc=json.loads(path.read_text());cases=doc['cases']
  self.assertEqual({c['config']['method'] for c in cases},set('none mean std_threshold ewma median quantile savitzky_golay wavelet kalman mz5_11'.split()))
  self.assertTrue({1,7,8,64,65}.issubset({len(c['data']['time']) for c in cases}))
  from generate_reference_fixtures import generate
  self.assertEqual(doc,generate(ROOT/'docs/deNoise_ms.py'))
