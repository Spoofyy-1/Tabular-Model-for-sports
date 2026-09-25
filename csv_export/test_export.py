import io
import os
import unittest
from unittest.mock import patch

import pandas as pd
import pyarrow as pa
import export as exporter


class CsvExportTests(unittest.TestCase):
    def test_ids_missing_text_and_nested_cells_roundtrip(self):
        batch = pa.record_batch({'id': pa.array(['00123', '00456']), 'count': pa.array([1, None], type=pa.int64()),
                                 'text': pa.array(['comma, quote" and\nnewline', '']),
                                 'nested': pa.array([[1, 2], None], type=pa.list_(pa.int64()))})
        frame = exporter.csv_batch(batch)
        buffer = io.StringIO()
        frame.to_csv(buffer, index=False, na_rep=exporter.NULL)
        buffer.seek(0)
        restored = pd.read_csv(buffer, dtype={'id': 'string'}, keep_default_na=False, na_values=[exporter.NULL])
        self.assertEqual(restored.id.tolist(), ['00123', '00456'])
        self.assertTrue(pd.isna(restored.loc[1, 'count']))
        self.assertEqual(restored.loc[1, 'text'], '')
        self.assertEqual(restored.loc[0, 'nested'], '[1,2]')
        self.assertEqual(restored.loc[0, 'text'], 'comma, quote" and\nnewline')

    def test_timestamp_timezone_and_null_sentinel_collision(self):
        batch = pa.record_batch({'date': pa.array([pd.Timestamp('2024-12-31T23:00:00Z')], type=pa.timestamp('us', tz='UTC'))})
        self.assertIn('+00:00', exporter.csv_batch(batch).to_csv(index=False))
        with self.assertRaises(ValueError):
            exporter.csv_batch(pa.record_batch({'text': [exporter.NULL]}))

    def test_tar_paths_and_local_guard(self):
        for path in ['../escape', '/absolute', 'dir/../../escape']:
            with self.assertRaises(ValueError):
                exporter.safe_name(path)
        with patch.dict(os.environ, {}, clear=True), patch.object(exporter.requests, 'get') as network:
            with self.assertRaises(RuntimeError):
                exporter.main()
            network.assert_not_called()


if __name__ == '__main__':
    unittest.main()
