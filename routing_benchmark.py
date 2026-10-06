"""Evaluate labeled routing cases. Optional argument: your own JSON fixture."""
import json
import pathlib
import sys
import tempfile
from core import scan_catalog, select_skill, extract_explicit_skill_names


def run(path):
    data = json.loads(path.read_text(encoding='utf-8'))
    with tempfile.TemporaryDirectory(prefix='skill-proof-routing-') as tmp:
        root = pathlib.Path(tmp)
        for i, skill in enumerate(data['skills']):
            folder = root / str(i)
            folder.mkdir()
            (folder / 'SKILL.md').write_text(
                '---\nname: ' + json.dumps(skill['name']) + '\ndescription: ' +
                json.dumps(skill['description'], ensure_ascii=False) + '\n---\nWorkflow instructions.\n', encoding='utf-8')
        catalog = scan_catalog({'fixture': root})
        correct = false_selections = misses = 0
        failures = []
        for i, case in enumerate(data['cases']):
            result = select_skill(catalog, case['query'], explicit_names=extract_explicit_skill_names(case['query']))
            actual = result.selected.skill.name if result.selected else None
            correct += actual == case['expected']
            false_selections += actual is not None and actual != case['expected']
            misses += actual is None and case['expected'] is not None
            if actual != case['expected']:
                failures.append({'case': i, 'expected': case['expected'], 'actual': actual, 'reason': result.reason})
        print(json.dumps({'correct': correct, 'total': len(data['cases']), 'false_selections': false_selections,
                          'misses': misses, 'failures': failures}, indent=2))
        return bool(failures)


if __name__ == '__main__':
    sys.exit(run(pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path(__file__).with_name('routing_cases.json')))
