#!/usr/bin/env python3
"""Select original StratX100 OOXML pages and replace verified text runs in place.

Standard library only. Never redraw or flatten template objects.
"""
import argparse
import hashlib
import json
import io
import math
import posixpath
import re
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape
from stratx100_images import slots as image_slots, replace_image
from stratx100_resources import resolve as resource_path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / 'assets/stratx100'
NS = {'p': 'http://schemas.openxmlformats.org/presentationml/2006/main',
      'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
      'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'}
REL = 'http://schemas.openxmlformats.org/package/2006/relationships'
TEXT = re.compile(r'(<a:t(?:\s[^>]*)?>)(.*?)(</a:t>)', re.S)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def relpath(owner):
    return posixpath.join(posixpath.dirname(owner), '_rels', posixpath.basename(owner) + '.rels') if owner else '_rels/.rels'


def target(owner, value):
    return value.lstrip('/') if value.startswith('/') else posixpath.normpath(posixpath.join(posixpath.dirname(owner), value))


def rels(parts, owner):
    raw = parts.get(relpath(owner))
    return list(ET.fromstring(raw)) if raw else []


def read_parts(path):
    with zipfile.ZipFile(path) as z:
        if z.testzip():
            raise ValueError('Corrupt source ZIP')
        return {n: z.read(n) for n in z.namelist()}


def slide_order(parts):
    mapping = {r.attrib['Id']: target('ppt/presentation.xml', r.attrib['Target']) for r in rels(parts, 'ppt/presentation.xml')}
    root = ET.fromstring(parts['ppt/presentation.xml'])
    return [mapping[n.attrib['{' + NS['r'] + '}id']] for n in root.findall('p:sldIdLst/p:sldId', NS)]


def inspect_page(parts, part):
    root = ET.fromstring(parts[part])
    texts = root.findall('.//a:t', NS)
    indices = {id(t): i for i, t in enumerate(texts)}
    shapes = []
    for shape in root.findall('.//p:sp', NS):
        c = shape.find('p:nvSpPr/p:cNvPr', NS)
        tx = shape.findall('.//a:t', NS)
        if tx:
            shapes.append({'shape_id': c.attrib.get('id') if c is not None else None,
                           'name': c.attrib.get('name') if c is not None else None,
                           'text': ''.join(t.text or '' for t in tx),
                           'runs': [{'index': indices[id(t)], 'text': t.text or ''} for t in tx]})
    chart_bindings = []
    chart_objects = []
    for rel in rels(parts, part):
        if rel.attrib['Type'].endswith('/chart'):
            cp = target(part, rel.attrib['Target'])
            cr = ET.fromstring(parts[cp])
            cns = {'c': 'http://schemas.openxmlformats.org/drawingml/2006/chart'}
            types=sorted({e.tag.split('}')[-1] for e in cr.iter() if e.tag.split('}')[-1].endswith('Chart')})
            chart_objects.append({'part':cp,'types':types,
                'static_annotation_warning': 'Point labels, arrows and mean lines may be static slide shapes; coordinate changes do not move them.' if any(t in types for t in ('bubbleChart','scatterChart')) else 'Static percentages, tables and conclusions on the slide do not update with the workbook.'})
            for ref in cr.findall('.//c:numRef', cns):
                formula = ref.findtext('c:f', namespaces=cns)
                points = ref.findall('c:numCache/c:pt', cns)
                chart_bindings.append({'part': cp, 'formula': formula,
                                       'before': [float(p.findtext('c:v', namespaces=cns)) for p in points]})
    size=ET.fromstring(parts['ppt/presentation.xml']).find('p:sldSz',NS)
    return {'part': part, 'sha256': sha(parts[part]), 'shapes': shapes, 'chart_values': chart_bindings,
            'chart_objects':chart_objects,
            'image_slots': image_slots(parts[part].decode(),[int(size.attrib[k]) for k in ('cx','cy')]),
            'all_runs': [{'index': i, 'text': t.text or ''} for i, t in enumerate(texts)],
            'dependencies': [{'id': r.attrib['Id'], 'type': r.attrib['Type'].split('/')[-1],
                              'part': target(part, r.attrib['Target'])} for r in rels(parts, part)]}


COLOR_ROLES = ('dk1','lt1','dk2','lt2','accent1','accent2','accent3','accent4','accent5','accent6','hlink','folHlink')


def slide_themes(parts):
    """Only presentation themes, not unrelated notes/workbook Office themes."""
    found=set()
    def follow(owner, kind):
        return [target(owner,r.attrib['Target']) for r in rels(parts,owner)
                if r.attrib['Type'].endswith('/'+kind) and r.attrib.get('TargetMode')!='External']
    for slide in slide_order(parts):
        for layout in follow(slide,'slideLayout'):
            for master in follow(layout,'slideMaster'):
                found.update(follow(master,'theme'))
    return sorted(found)


def theme_colors(parts):
    return {n:{v.tag.split('}')[-1]:list(v)[0].attrib for v in ET.fromstring(parts[n]).find('a:themeElements/a:clrScheme',NS)}
            for n in slide_themes(parts)}


def replace_palette(parts, palette):
    """Replace theme slots only; keep all transforms, media and geometry intact."""
    changes=[]
    for name in slide_themes(parts):
        raw=parts[name].decode()
        for role,value in palette.items():
            color=value.lstrip('#').upper()
            pattern=r'(<a:'+role+r'>)(.*?)(</a:'+role+r'>)'
            def replace(m):
                replacement='<a:srgbClr val="'+color+'"/>'
                if m[2]!=replacement:
                    changes.append({'part':name,'role':role,'before_xml':m[2],'after':color})
                return m[1]+replacement+m[3]
            raw,count=re.subn(pattern,replace,raw,flags=re.S)
            if count!=1:raise ValueError('Cannot resolve theme color slot '+role+' in '+name)
        parts[name]=raw.encode()
    if not slide_themes(parts):raise ValueError('No presentation theme found')
    return changes


def catalog():
    return json.loads((ASSETS / 'catalog.json').read_text())


def source(cat, theme):
    matches = [x for x in cat['themes'] if theme in (x['id'], x['name'])]
    if len(matches) != 1:
        raise ValueError('Unknown theme; use list --themes')
    entry = matches[0]
    path = resource_path(entry['file'])
    if not path.is_file():
        raise ValueError('Template resource missing. Run: python scripts/stratx100_resources.py fetch --theme ' + entry['id'] + ' ; or import the offline resource ZIP')
    if sha(path.read_bytes()) != entry['sha256']:
        raise ValueError('Source hash changed; re-index and review it before use')
    return path, entry


def subset(parts, selected):
    """Preserve selected slide bytes; prune all unreachable package parts."""
    parts = dict(parts)
    all_slides = set(slide_order(parts))
    chosen = set(selected)
    pres = parts['ppt/presentation.xml'].decode()
    ridmap = {target('ppt/presentation.xml', r.attrib['Target']): r.attrib['Id'] for r in rels(parts, 'ppt/presentation.xml')}
    tags = re.findall(r'<p:sldId\b[^>]*/>', pres)
    byrid = {re.search(r'r:id="([^"]+)"', t)[1]: t for t in tags}
    pres = re.sub(r'<p:sldIdLst>.*?</p:sldIdLst>', '<p:sldIdLst>' + ''.join(byrid[ridmap[p]] for p in selected) + '</p:sldIdLst>', pres, flags=re.S)
    # Original navigation groups cannot describe a new selection.
    pres = re.sub(r'<p:custShowLst\b.*?</p:custShowLst>', '', pres, flags=re.S)
    pres = re.sub(r'<p:extLst\b.*?</p:extLst>', '', pres, flags=re.S)
    parts['ppt/presentation.xml'] = pres.encode()
    for name in list(parts):
        if not name.endswith('.rels'):
            continue
        owner = '' if name == '_rels/.rels' else posixpath.join(posixpath.dirname(posixpath.dirname(name)), posixpath.basename(name)[:-5])
        raw = parts[name].decode()
        removed = []
        def filter_rel(m):
            el = ET.fromstring(m[0])
            dest = target(owner, el.attrib['Target'])
            if el.attrib.get('TargetMode') != 'External' and dest in all_slides - chosen:
                removed.append(el.attrib['Id'])
                return ''
            return m[0]
        raw = re.sub(r'<Relationship\b[^>]*/>', filter_rel, raw)
        parts[name] = raw.encode()
        if removed and owner in parts:
            body = parts[owner].decode()
            for rid in removed:
                body = re.sub(r'<a:hlink(?:Click|MouseOver)\b(?=[^>]*\br:id="' + re.escape(rid) + r'")[^>]*(?:/>|>.*?</a:hlink(?:Click|MouseOver)>)', '', body, flags=re.S)
            parts[owner] = body.encode()
    keep = set()
    todo = ['']
    while todo:
        owner = todo.pop()
        if owner and owner in keep:
            continue
        if owner:
            if owner not in parts:
                raise ValueError('Missing relationship target: ' + owner)
            keep.add(owner)
        rp = relpath(owner)
        if rp in parts:
            keep.add(rp)
            for r in rels(parts, owner):
                if r.attrib.get('TargetMode') != 'External':
                    todo.append(target(owner, r.attrib['Target']))
    ct = parts['[Content_Types].xml'].decode()
    ct = re.sub(r'<Override\b[^>]*/>', lambda m: m[0] if ET.fromstring(m[0]).attrib['PartName'].lstrip('/') in keep else '', ct)
    result = {n: parts[n] for n in keep}
    result['[Content_Types].xml'] = ct.encode()
    if 'docProps/app.xml' in result:
        result['docProps/app.xml'] = re.sub(rb'<Slides>\d+</Slides>', f'<Slides>{len(selected)}</Slides>'.encode(), result['docProps/app.xml'])
    return result


def verify_chart_data(parts, part):
    """A changed chart must not retain a stale numeric cache in another series/axis."""
    cn={'c':'http://schemas.openxmlformats.org/drawingml/2006/chart'}
    sn={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    packages=[target(part,r.attrib['Target']) for r in rels(parts,part) if r.attrib['Type'].endswith('/package')]
    if len(packages)!=1 or not packages[0].endswith('.xlsx'):
        raise ValueError('Chart must have one embedded XLSX')
    with zipfile.ZipFile(io.BytesIO(parts[packages[0]])) as z:
        book={n:z.read(n) for n in z.namelist()}
    sheets=ET.fromstring(book['xl/workbook.xml']).findall('s:sheets/s:sheet',sn)
    sheet_parts={r.attrib['Id']:target('xl/workbook.xml',r.attrib['Target']) for r in rels(book,'xl/workbook.xml')}
    byname={s.attrib['name']:sheet_parts[s.attrib['{'+NS['r']+'}id']] for s in sheets}
    for ref in ET.fromstring(parts[part]).findall('.//c:numRef',cn):
        formula=ref.findtext('c:f',namespaces=cn)
        m=re.fullmatch(r"(?:'([^']+)'|([^!]+))!\$([A-Z]+)\$(\d+):\$([A-Z]+)\$(\d+)",formula or '')
        if not m or m[3]!=m[5]:
            raise ValueError('Cannot verify all numeric references in chart: '+str(formula))
        name=m[1] or m[2];first,last=int(m[4]),int(m[6])
        points=ref.findall('c:numCache/c:pt',cn)
        if [int(p.attrib['idx']) for p in points]!=list(range(last-first+1)):
            raise ValueError('Sparse numeric chart reference requires native review: '+formula)
        sheet=ET.fromstring(book[byname[name]])
        for row,point in zip(range(first,last+1),points):
            cell=m[3]+str(row);node=sheet.find('.//s:c[@r="'+cell+'"]',sn)
            if node is None or node.attrib.get('t','n')!='n' or node.find('s:f',sn) is not None:
                raise ValueError('Cannot verify numeric workbook cell '+formula+' '+cell)
            actual=float(node.findtext('s:v',namespaces=sn))
            cached=float(point.findtext('c:v',namespaces=cn))
            if not math.isfinite(actual) or actual!=cached:
                raise ValueError(f'Chart/workbook mismatch: {part} {formula} {cell}: cache={cached}, workbook={actual}')


def replace_chart(parts, binding):
    """Update one numeric chart reference and the same cells in its embedded XLSX."""
    part, formula = binding['part'], binding['formula']
    if not re.fullmatch(r'ppt/charts/chart\d+\.xml', part) or part not in parts:
        raise ValueError('Chart is not part of the selected pages')
    verify_chart_data(parts,part)
    before, after = binding['before'], binding['after']
    if before==after:
        raise ValueError('Chart replacement has no changed values')
    if len(before) != len(after) or not after or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in after):
        raise ValueError('Chart values must be finite and retain the original category count')
    match = re.fullmatch(r"(?:'([^']+)'|([^!]+))!\$([A-Z]+)\$(\d+):\$([A-Z]+)\$(\d+)", formula)
    if not match:
        raise ValueError('Only a contiguous embedded worksheet range is supported')
    sheet, col, first, endcol, last = match[1] or match[2], match[3], int(match[4]), match[5], int(match[6])
    if col != endcol or last-first+1 != len(after):
        raise ValueError('Only same-column numeric ranges are supported')
    cn = {'c': 'http://schemas.openxmlformats.org/drawingml/2006/chart'}
    seen = []
    def patch_ref(m):
        r = ET.fromstring('<root xmlns:c="' + cn['c'] + '">' + m[0] + '</root>')[0]
        if r.findtext('c:f', namespaces=cn) != formula:
            return m[0]
        points = r.findall('c:numCache/c:pt', cn)
        values = [float(p.findtext('c:v', namespaces=cn)) for p in points]
        if values != before or [int(p.attrib['idx']) for p in points] != list(range(len(before))):
            raise ValueError('Stale or sparse chart cache; inspect before editing')
        index = iter(after)
        seen.append(True)
        return re.sub(r'(<c:v>).*?(</c:v>)', lambda v: v[1] + str(next(index)) + v[2], m[0], flags=re.S)
    edited = re.sub(r'<c:numRef>.*?</c:numRef>', patch_ref, parts[part].decode(), flags=re.S)
    if not seen:
        raise ValueError('Chart formula not found')
    packages = [target(part, r.attrib['Target']) for r in rels(parts, part) if r.attrib['Type'].endswith('/package')]
    if len(packages) != 1 or not packages[0].endswith('.xlsx'):
        raise ValueError('Chart must have one embedded XLSX; cache-only changes are refused')
    with zipfile.ZipFile(io.BytesIO(parts[packages[0]])) as z:
        book = {n: z.read(n) for n in z.namelist()}
    sn = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    sheets = ET.fromstring(book['xl/workbook.xml']).findall('s:sheets/s:sheet', sn)
    sid = next((s.attrib['{' + NS['r'] + '}id'] for s in sheets if s.attrib['name'] == sheet), None)
    ws = next((target('xl/workbook.xml', r.attrib['Target']) for r in rels(book, 'xl/workbook.xml') if r.attrib['Id'] == sid), None)
    if not ws:
        raise ValueError('Embedded worksheet not found')
    raw = book[ws].decode()
    for row, old, value in zip(range(first, last+1), before, after):
        cell = f'{col}{row}'
        pattern = r'<c\b(?=[^>]*\br="' + cell + r'")[^>]*>.*?</c>'
        matches = list(re.finditer(pattern, raw, re.S))
        if len(matches) != 1:
            raise ValueError('Worksheet cell missing or repeated: ' + cell)
        node = ET.fromstring(matches[0][0])
        if node.attrib.get('t', 'n') != 'n' or node.find('f') is not None or float(node.findtext('v')) != old:
            raise ValueError('Worksheet/cache mismatch or formula cell: ' + cell)
        replacement = re.sub(r'<v>.*?</v>', '<v>' + str(value) + '</v>', matches[0][0], flags=re.S)
        raw = raw[:matches[0].start()] + replacement + raw[matches[0].end():]
    book[ws] = raw.encode()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        for n, b in book.items():
            z.writestr(n, b)
    parts[packages[0]] = buf.getvalue()
    parts[part] = edited.encode()
    return {'part': part, 'formula': formula, 'before': before, 'after': after, 'workbook': packages[0]}


def check_keys(value, allowed, required, label):
    if not isinstance(value, dict):
        raise ValueError(label + ' must be an object')
    unknown = set(value) - set(allowed)
    missing = set(required) - set(value)
    if unknown or missing:
        raise ValueError(f'{label}: unknown fields {sorted(unknown)}, missing fields {sorted(missing)}')


def validate_plan(plan):
    check_keys(plan, ('mode','theme','slides','chart_values','palette'), ('mode','slides'), 'plan')
    if 'palette' in plan:
        check_keys(plan['palette'], COLOR_ROLES, (), 'palette')
        if not plan['palette'] or any(not isinstance(v,str) or not re.fullmatch(r'#?[0-9A-Fa-f]{6}',v) for v in plan['palette'].values()):
            raise ValueError('palette must contain theme slots with six-digit hex colors')
    if not isinstance(plan['slides'], list) or not plan['slides']:
        raise ValueError('slides must be a nonempty array')
    for item in plan['slides']:
        check_keys(item, ('id','text_runs','images'), ('id',), 'slide')
        for name in ('text_runs','images'):
            if not isinstance(item.get(name, []), list):
                raise ValueError(name + ' must be an array')
        for binding in item.get('text_runs', []):
            check_keys(binding, ('index','before','after'), ('index','before','after'), 'text run')
            if type(binding['index']) is not int or not all(isinstance(binding[k], str) for k in ('before','after')):
                raise ValueError('Text index must be an integer; before/after must be strings')
        ids=[]
        for binding in item.get('images', []):
            keys=('shape_id','before_sha256','path','fit','alt')
            check_keys(binding, keys+('remove_placeholder',), keys, 'image')
            if not all(isinstance(binding[k],str) for k in ('before_sha256','path','fit','alt')) or not binding['alt'].strip():
                raise ValueError('Image path, hash, fit and descriptive alt must be strings')
            ids.append(str(binding['shape_id']))
            if not isinstance(binding.get('remove_placeholder', []),list):
                raise ValueError('remove_placeholder must be an array')
            for ph in binding.get('remove_placeholder', []):
                check_keys(ph, ('shape_id','before_sha256'), ('shape_id','before_sha256'), 'placeholder')
        if len(set(ids)) != len(ids):
            raise ValueError('Duplicate image target')
    if not isinstance(plan.get('chart_values', []), list):
        raise ValueError('chart_values must be an array')
    for binding in plan.get('chart_values', []):
        keys=('part','formula','before','after')
        check_keys(binding, keys, keys, 'chart binding')
        if not all(isinstance(binding[k],list) for k in ('before','after')):
            raise ValueError('Chart before/after must be arrays')


def build(plan, output):
    validate_plan(plan)
    if output.exists() or output.with_suffix('.receipt.json').exists():
        raise ValueError('Refusing to overwrite output or receipt')
    cat = catalog()
    path, theme = source(cat, plan.get('theme', 'morning-bay-haze'))
    original = read_parts(path)
    entries = {x['id']: x for x in cat['templates']}
    mode = plan.get('mode')
    if mode not in ('reproduction', 'adaptation'):
        raise ValueError('mode must be reproduction or adaptation (draft pending content/visual QA)')
    selected, changes = [], []
    for item in plan['slides']:
        entry = entries[str(item['id']).zfill(3)]
        part = slide_order(original)[entry['source_page'] - 1]
        if part in selected:
            raise ValueError('Duplicate template in one build: make separate files or duplicate in native editor')
        selected.append(part)
    result = subset(original, selected)
    image_changes=[]
    for item, part in zip(plan['slides'], selected):
        for binding in item.get('images', []):
            image_changes.append(replace_image(result, part, binding))
        replacements = item.get('text_runs', [])
        lookup = {int(r['index']): r for r in replacements}
        if len(lookup) != len(replacements):
            raise ValueError('Duplicate text run index')
        raw = result[part].decode()
        nodes = ET.fromstring(result[part]).findall('.//a:t', NS)
        if any(i < 0 or i >= len(nodes) for i in lookup):
            raise ValueError('Text index out of range')
        counter = iter(range(len(nodes)))
        def replace(m):
            i = next(counter)
            if i not in lookup:
                return m[0]
            r = lookup[i]
            before = nodes[i].text or ''
            if before != r['before']:
                raise ValueError(f'Stale text binding {part} run {i}: {before!r}')
            after = r['after']
            if not isinstance(after, str) or any(ord(c) < 32 and c not in '\t\n\r' for c in after):
                raise ValueError('Invalid XML text')
            if after != before:
                changes.append({'part': part, 'index': i, 'before': before, 'after': after})
            return m[1] + escape(after) + m[3]
        result[part] = TEXT.sub(replace, raw).encode()
    chart_changes = [replace_chart(result, binding) for binding in plan.get('chart_values', [])]
    palette_changes = replace_palette(result,plan['palette']) if 'palette' in plan else []
    if mode == 'reproduction' and (changes or chart_changes or image_changes or palette_changes):
        raise ValueError('Reproduction must be unedited; use adaptation')
    if mode == 'adaptation' and not (changes or chart_changes or image_changes or palette_changes):
        raise ValueError('Adaptation needs actual replacements')
    for n, raw in result.items():
        if n.endswith(('.xml', '.rels')):
            ET.fromstring(raw)
    if slide_order(result) != selected:
        raise ValueError('Slide selection/order mismatch')
    unchanged = sum(result[n] == original.get(n) for n in result)
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, 'x', zipfile.ZIP_DEFLATED) as z:
        for n in sorted(result):
            z.writestr(n, result[n])
    receipt = {'mode': mode, 'status': 'draft_pending_visual_review' if mode == 'reproduction' else 'draft_pending_content_and_visual_review',
               'theme': theme['id'], 'source_sha256': theme['sha256'], 'output_sha256': sha(output.read_bytes()),
               'selected_parts': selected, 'parts': len(result), 'unchanged_parts': unchanged,
               'changes': changes, 'chart_changes': chart_changes, 'image_changes': image_changes, 'palette_changes':palette_changes,
               'palette_followup':'Review text contrast, gradients, charts and fixed-color images in the target client. Media bytes are not recolored.' if palette_changes else None,
               'chart_followup': 'Check static percentages, tables, conclusions, point labels and mean lines against every changed series. These slide objects are not updated automatically.' if chart_changes else None,
               'native_charts': sum(bool(re.fullmatch(r'ppt/charts/chart\d+\.xml', n)) for n in result),
               'unselected_slide_parts_remaining': sorted(set(slide_order(original)) & set(result) - set(selected)),
               'limits': ['Text replacement retains run styling and geometry; longer text needs rendering.',
                          'Original sample numbers, chart workbooks, images and off-canvas objects remain unless explicitly edited. Audit before client delivery.',
                          'Template reproduction is not factual customer-case validation.']}
    output.with_suffix('.receipt.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    print(json.dumps({'output': str(output), 'slides': len(selected), 'status': receipt['status']}, ensure_ascii=False))


def audit_file(path):
    """Read-only package/data checks; deliberately not a visual or factual pass."""
    parts = read_parts(path)
    findings = []
    for name, raw in parts.items():
        if name.endswith(('.xml', '.rels')):
            ET.fromstring(raw)
        if not name.endswith('.rels'):
            continue
        owner = '' if name == '_rels/.rels' else posixpath.join(posixpath.dirname(posixpath.dirname(name)), posixpath.basename(name)[:-5])
        for rel in rels(parts, owner):
            if rel.attrib.get('TargetMode') == 'External':
                findings.append({'severity':'review', 'part':owner, 'kind':'external_relationship',
                                 'type':rel.attrib['Type'].split('/')[-1]})
            elif target(owner, rel.attrib['Target']) not in parts:
                findings.append({'severity':'error', 'part':owner, 'kind':'missing_target',
                                 'target':target(owner, rel.attrib['Target'])})
    order = slide_order(parts)
    charts = sorted(n for n in parts if re.fullmatch(r'ppt/charts/chart\d+\.xml', n))
    verified = []
    for part in charts:
        try:
            verify_chart_data(parts, part)
            verified.append(part)
        except (ValueError, KeyError, TypeError, zipfile.BadZipFile) as exc:
            findings.append({'severity':'error', 'part':part, 'kind':'chart_data', 'detail':str(exc)})
    pages=[]
    for part in order:
        page=inspect_page(parts, part)
        pages.append({'part':part, 'text_runs':len(page['all_runs']),
                      'native_charts':len(page['chart_objects']),
                      'off_canvas_or_hidden_objects':sum(not x.get('on_canvas',False) or x.get('hidden',False) for x in page['image_slots']),
                      'chart_followup':page['chart_objects']})
    return {'input':str(path), 'sha256':sha(path.read_bytes()),
            'structural_data_status':'failed' if any(f['severity']=='error' for f in findings) else 'passed',
            'delivery_status':'pending_visual_and_content_review',
            'slides':len(order), 'native_charts':len(charts), 'verified_numeric_charts':verified,
            'findings':findings, 'pages':pages,
            'limits':['Numeric caches and workbook cells are checked; category labels and static annotations require review.',
                      'No font, clipping, overlap, sample-residue or customer-fact acceptance is inferred from this audit.',
                      'Opening/editing in WPS or PowerPoint is a separate test.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    ls = sub.add_parser('list')
    ls.add_argument('--query', default='')
    ls.add_argument('--themes', action='store_true')
    ins = sub.add_parser('inspect')
    ins.add_argument('id')
    ins.add_argument('--theme', default='morning-bay-haze')
    b = sub.add_parser('build')
    b.add_argument('plan', type=Path)
    b.add_argument('output', type=Path)
    au = sub.add_parser('audit', help='Read-only package and numeric chart checks; not visual acceptance')
    au.add_argument('input', type=Path)
    args = parser.parse_args()
    if args.command == 'audit':
        report=audit_file(args.input.resolve())
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if report['structural_data_status']=='failed':
            sys.exit(2)
        return
    if args.command == 'build':
        build(json.loads(args.plan.read_text()), args.output.resolve())
        return
    cat = catalog()
    if args.command == 'list':
        data = cat['themes'] if args.themes else [e for e in cat['templates'] if not args.query or args.query.lower() in json.dumps(e, ensure_ascii=False).lower()]
    else:
        entry = next(e for e in cat['templates'] if e['id'] == args.id.zfill(3))
        path, _ = source(cat, args.theme)
        parts = read_parts(path)
        data = {'template': entry, 'page': inspect_page(parts, slide_order(parts)[entry['source_page'] - 1]), 'theme_colors':theme_colors(parts),
                'guide': (ROOT / entry['guide']).read_text()}
    print(json.dumps(data, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, KeyError, StopIteration) as exc:
        sys.exit('ERROR: ' + str(exc))
