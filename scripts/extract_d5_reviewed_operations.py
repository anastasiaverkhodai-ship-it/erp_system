"""Reproduce civil profession operations from pinned PDF table extraction.

Extraction is evidence, not an independent normative approval.
"""
import json
from collections import Counter


def clean_cell(value):
    return ' '.join((value or '').replace('-\n', '-').split())


def extract_operations(number, evidence_dir):
    groups = [[], []]
    appendix = -1
    old = None
    errors = []
    tables = json.loads((evidence_dir / f'amendment_{number}_tables.json').read_text())
    for table in tables:
        page = table['page']
        # Amendment 16 starts a separate military section on PDF page 19.
        if number == 16 and page >= 19:
            continue
        for raw in table['rows']:
            row = [clean_cell(value) for value in raw]
            if len(row) < 5:
                continue
            if row[0] == 'C':
                row[0] = 'С'
            if all(not value or value.isdigit() for value in row):
                continue
            while row and not row[-1]:
                row.pop()
            if len(row) < 5 or any('Професійна назва' in value for value in row):
                continue
            if row[0] == 'С':
                if not groups[0] or (groups[0][-1][0] != 'remove' and appendix == 0):
                    appendix += 1
                elif appendix == -1:
                    appendix = 0
                if appendix > 1:
                    errors.append(('appendix', page, row))
                    continue
                groups[appendix].append(('remove', row[1], row[-1]))
                continue
            if appendix < 0:
                continue
            if row[0] in ('У', 'В'):
                groups[appendix].append(('add', row[1], row[-1]))
                continue
            if len(row) >= 6 and row[1] == 'Є':
                if old:
                    errors.append(('unmatched old', old))
                old = (row[2], row[-1])
                continue
            if len(row) >= 6 and row[1].casefold() == 'має бути':
                if not old:
                    errors.append(('missing old', page, row))
                    continue
                groups[appendix].append(('replace', *old, row[2], row[-1]))
                old = None
                continue
            if row[-1] and not any(row[:-1]):
                if old:
                    old = (old[0], old[1] + ' ' + row[-1])
                elif groups[appendix]:
                    previous = groups[appendix][-1]
                    groups[appendix][-1] = (*previous[:-1], previous[-1] + ' ' + row[-1])
                else:
                    errors.append(('orphan continuation', page, row))
                continue
            if any(row):
                errors.append(('unparsed', page, row))
    if old:
        errors.append(('last old', old))
    if number == 14:
        # PDF page 6: detector split one name across two cells; source image
        # and alphabetical Appendix B both show the complete name below.
        groups[0] = [
            ('remove', '3432', 'Державний уповноважений')
            if event == ('remove', '3432', 'уповноважений') else event
            for event in groups[0]
        ]
    a, b = map(Counter, groups)
    return {
        'amendment': number, 'A': groups[0], 'B': groups[1], 'errors': errors,
        'a_only': list((a-b).elements()), 'b_only': list((b-a).elements()),
    }
