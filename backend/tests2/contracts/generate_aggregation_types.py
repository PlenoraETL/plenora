"""Rigenera la tabella eseguendo op_aggregate su campioni tipizzati."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import pandas as pd
from app.core.manipola.transforms.aggregation import op_aggregate
from app.core.manipola.canvas_schema_runtime import build_columns_with_types

DESTINATION = Path(__file__).parents[2] / 'app/shared/execution/aggregation_types.json'
FUNCTIONS = ('count', 'size', 'nunique', 'sum', 'mean', 'avg', 'median', 'std', 'var', 'min', 'max', 'first', 'last', 'concat')


def generate():
    samples = {
        'int': [1, 3, 5], 'real': [1.5, 3.5, 5.5], 'bool': [True, False, True],
        'str': ['a', 'b', 'c'], 'geometry': ['POINT (0 0)', 'POINT (1 1)', 'POINT (2 2)'],
        'date': pd.to_datetime(['2024-01-01', '2024-01-03', '2024-01-05']),
        'datetime': pd.to_datetime(['2024-01-01 01:00', '2024-01-03 03:00', '2024-01-05 05:00']),
    }
    table = {}
    for function in FUNCTIONS:
        table[function] = {}
        for kind, values in samples.items():
            frame = pd.DataFrame({'g': [1, 1, 1], 'v': values})
            try:
                result = op_aggregate(frame, ['g'], [{'column': 'v', 'function': function}])
                outcome = build_columns_with_types(result, {})[1]['type']
            except Exception:
                outcome = 'error'
            table[function][kind] = outcome
    return table


if __name__ == '__main__':
    DESTINATION.write_text(json.dumps(generate(), indent=2, sort_keys=True) + '\n', encoding='utf-8')
