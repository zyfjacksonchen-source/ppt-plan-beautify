"""Targeted PNG fills for original, unrotated template objects (stdlib only)."""
import hashlib
import posixpath
import re
import struct
import zlib
from pathlib import Path
from xml.etree import ElementTree as ET
from xml.sax.saxutils import quoteattr

NS = {'p': 'http://schemas.openxmlformats.org/presentationml/2006/main',
      'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
      'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'}
BLOCK = re.compile(r'<p:(sp|pic)\b[^>]*>.*?</p:\1>', re.S)


def object_blocks(raw):
    for m in BLOCK.finditer(raw):
        root = ET.fromstring('<root ' + ' '.join('xmlns:' + k + '="' + v + '"' for k, v in NS.items()) +
                             ' xmlns:a14="http://schemas.microsoft.com/office/drawing/2010/main"'
                             ' xmlns:asvg="http://schemas.microsoft.com/office/drawing/2016/SVG/main"'
                             '>' + m[0] + '</root>')[0]
        pr = root.find('.//p:cNvPr', NS)
        if pr is not None:
            yield pr.attrib['id'], m, root


def slots(raw, canvas_size=None):
    """Group transforms map local extents to the slide; report effective boxes."""
    root = ET.fromstring(raw)
    boxes = {}
    def walk(parent, sx=1., sy=1., tx=0., ty=0., rotated=False):
        for child in parent:
            local = child.tag.split('}')[-1]
            if local == 'grpSp':
                tr = child.find('p:grpSpPr/a:xfrm', NS)
                if tr is None:
                    walk(child, sx, sy, tx, ty, True)
                    continue
                off, ext, co, ce = [tr.find('a:' + x, NS) for x in ('off', 'ext', 'chOff', 'chExt')]
                gx = int(ext.attrib['cx']) / int(ce.attrib['cx'])
                gy = int(ext.attrib['cy']) / int(ce.attrib['cy'])
                bad = rotated or any(tr.attrib.get(k, '0') not in ('0', 'false') for k in ('rot', 'flipH', 'flipV'))
                walk(child, sx*gx, sy*gy, tx+sx*(int(off.attrib['x'])-gx*int(co.attrib['x'])),
                     ty+sy*(int(off.attrib['y'])-gy*int(co.attrib['y'])), bad)
            elif local in ('sp', 'pic'):
                pr = child.find('.//p:cNvPr', NS)
                tr = child.find('p:spPr/a:xfrm', NS)
                if pr is None or tr is None:
                    continue
                off, ext = tr.find('a:off', NS), tr.find('a:ext', NS)
                if off is None or ext is None:
                    continue
                x,y,ww,hh = tx+sx*int(off.attrib['x']),ty+sy*int(off.attrib['y']),sx*int(ext.attrib['cx']),sy*int(ext.attrib['cy'])
                boxes[pr.attrib['id']] = {'box_emu': [round(x),round(y),round(ww),round(hh)],
                    'supported_transform': not rotated and not any(tr.attrib.get(k, '0') not in ('0','false') for k in ('rot','flipH','flipV'))}
    tree = root.find('p:cSld/p:spTree', NS)
    if tree is not None:
        walk(tree)
    result=[]
    for oid, m, node in object_blocks(raw):
        if oid not in boxes:
            continue
        pr = node.find('.//p:cNvPr', NS)
        text = ''.join(n.text or '' for n in node.findall('.//a:t', NS))
        hidden = pr.attrib.get('hidden') in ('1','true')
        geo = node.find('p:spPr/a:prstGeom', NS)
        x,y,w,h=boxes[oid]['box_emu']
        on_canvas=canvas_size is None or (x>=0 and y>=0 and x+w<=canvas_size[0] and y+h<=canvas_size[1])
        eligible = not hidden and on_canvas and w>0 and h>0 and not text.strip() and boxes[oid]['supported_transform']
        if node.tag.endswith('}sp'):
            eligible = eligible and geo is not None and geo.attrib.get('prst') in ('rect','roundRect')
        result.append({'shape_id':oid,'name':pr.attrib.get('name'),'kind':node.tag.split('}')[-1],
                       'before_sha256':hashlib.sha256(m[0].encode()).hexdigest(),'text':text,'hidden':hidden,
                       'on_canvas':on_canvas,'image_fill_supported':eligible,**boxes[oid]})
    return result


def replace_image(parts, part, binding):
    raw=parts[part].decode()
    size=ET.fromstring(parts['ppt/presentation.xml']).find('p:sldSz',NS)
    canvas=[int(size.attrib[k]) for k in ('cx','cy')]
    available={s['shape_id']:s for s in slots(raw,canvas)}
    oid=str(binding['shape_id'])
    if oid not in available or not available[oid]['image_fill_supported']:
        raise ValueError('Image target must be a visible, empty rectangle/picture with an unrotated group transform')
    slot=available[oid]
    if slot['before_sha256'] != binding['before_sha256']:
        raise ValueError('Stale image target binding; inspect the current theme')
    path=Path(binding['path'])
    if not path.is_absolute():
        raise ValueError('Image path must be absolute')
    data=path.read_bytes()
    if len(data)<33 or data[:8]!=b'\x89PNG\r\n\x1a\n' or data[12:16]!=b'IHDR':
        raise ValueError('Image fills currently require a PNG original')
    width,height=struct.unpack_from('>II',data,16)
    if not width or not height:
        raise ValueError('PNG has invalid dimensions')
    cursor=8;seen=set();compressed=[];palette=None
    while cursor<len(data):
        if cursor+12>len(data):raise ValueError('Truncated PNG chunk')
        length=struct.unpack_from('>I',data,cursor)[0]
        end=cursor+12+length
        if end>len(data):raise ValueError('Truncated PNG data')
        kind=data[cursor+4:cursor+8]
        payload=data[cursor+8:end-4]
        crc=struct.unpack_from('>I',data,end-4)[0]
        if zlib.crc32(data[cursor+4:end-4])&0xffffffff != crc:raise ValueError('Invalid PNG checksum')
        if kind==b'IDAT':compressed.append(payload)
        if kind==b'PLTE':palette=payload
        seen.add(kind);cursor=end
        if kind==b'IEND':break
    if not {b'IHDR',b'IDAT',b'IEND'}<=seen or cursor!=len(data):raise ValueError('Incomplete PNG')
    depth,colour,compression,filter_method,interlace=struct.unpack_from('>BBBBB',data,24)
    valid_depths={0:(1,2,4,8,16),2:(8,16),3:(1,2,4,8),4:(8,16),6:(8,16)}
    if colour not in valid_depths or depth not in valid_depths[colour] or compression or filter_method or interlace not in (0,1):
        raise ValueError('Unsupported or invalid PNG encoding')
    if colour==3 and (not palette or len(palette)%3 or len(palette)//3>2**depth):
        raise ValueError('Invalid PNG palette')
    channels={0:1,2:3,3:1,4:2,6:4}[colour]
    passes=[(0,0,1,1)] if not interlace else [(0,0,8,8),(4,0,8,8),(0,4,4,8),(2,0,4,4),(0,2,2,4),(1,0,2,2),(0,1,1,2)]
    scanlines=[]
    for x,y,dx,dy in passes:
        pw=max(0,(width-x+dx-1)//dx);ph=max(0,(height-y+dy-1)//dy)
        if pw and ph:scanlines.append((ph,(pw*channels*depth+7)//8+1))
    expected=sum(rows*stride for rows,stride in scanlines)
    if expected>128*1024*1024:raise ValueError('PNG decoded data exceeds 128 MiB; use a smaller original')
    try:
        decoder=zlib.decompressobj()
        decoded=decoder.decompress(b''.join(compressed),expected+1)
        if len(decoded)!=expected or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
            raise ValueError('PNG decompressed scanline size mismatch')
    except zlib.error as exc:
        raise ValueError('PNG image data cannot be decompressed') from exc
    start=0
    for rows,stride in scanlines:
        if any(decoded[start+i*stride]>4 for i in range(rows)):
            raise ValueError('Invalid PNG scanline filter')
        start+=rows*stride
    fit=binding['fit']
    if fit not in ('contain','cover'):
        raise ValueError('Image fit must be contain or cover')
    digest=hashlib.sha256(data).hexdigest()
    media='ppt/media/stratx-'+digest+'.png'
    rp=posixpath.join(posixpath.dirname(part),'_rels',posixpath.basename(part)+'.rels')
    relationships=parts[rp].decode()
    existing={n.attrib['Id'] for n in ET.fromstring(relationships)}
    number=1
    while 'rIdStratx'+str(number) in existing:
        number+=1
    rid='rIdStratx'+str(number)
    relation='<Relationship Id="'+rid+'" Type="'+NS['r']+'/image" Target="'+posixpath.relpath(media,posixpath.dirname(part))+'"/>'
    relationships=relationships.replace('</Relationships>',relation+'</Relationships>')
    bw,bh=slot['box_emu'][2:]
    ratio=(width/height)/(bw/bh)
    crop=''
    if fit=='cover':
        margin=round((1-1/ratio)*50000) if ratio>1 else round((1-ratio)*50000)
        crop=f' l="{margin}" r="{margin}"' if ratio>1 else f' t="{margin}" b="{margin}"'
    fragment='<a:blip r:embed="'+rid+'"/><a:srcRect'+crop+'/><a:stretch><a:fillRect/></a:stretch>'
    found=next(m for sid,m,_ in object_blocks(raw) if sid==oid)
    block=found[0]
    transform=re.search(r'<a:xfrm\b[^>]*>.*?</a:xfrm>',block,re.S)[0]
    if fit=='contain':
        # Use actual picture bounds. Some renderers ignore stretch fillRect margins.
        tr=ET.fromstring('<root xmlns:a="'+NS['a']+'">'+transform+'</root>')[0]
        off,ext=tr.find('a:off',NS),tr.find('a:ext',NS)
        x,y=int(off.attrib['x']),int(off.attrib['y'])
        cx,cy=int(ext.attrib['cx']),int(ext.attrib['cy'])
        nw,nh=(cx,round(cy/ratio)) if ratio>1 else (round(cx*ratio),cy)
        x+=(cx-nw)//2;y+=(cy-nh)//2
        transform=re.sub(r'<a:off\b[^>]*/>',f'<a:off x="{x}" y="{y}"/>',transform)
        transform=re.sub(r'<a:ext\b[^>]*/>',f'<a:ext cx="{nw}" cy="{nh}"/>',transform)
    alt=binding['alt']
    inserted=None
    if slot['kind']=='pic':
        block=re.sub(r'<p:blipFill\b[^>]*>.*?</p:blipFill>','<p:blipFill>'+fragment+'</p:blipFill>',block,flags=re.S)
        block=re.sub(r'<a:xfrm\b[^>]*>.*?</a:xfrm>',transform,block,count=1,flags=re.S)
        block=re.sub(r'<p:cNvPr\b[^>]*>',lambda m:re.sub(r'\sdescr="[^"]*"','',m[0])[:-2]+' descr='+quoteattr(alt)+'/>' if m[0].endswith('/>') else re.sub(r'\sdescr="[^"]*"','',m[0])[:-1]+' descr='+quoteattr(alt)+'>',block,count=1)
    else:
        # Keep the original frame and its outline; insert an independently editable
        # picture at the same z-position, before following labels/placeholder icons.
        inserted=str(max(int(e.attrib['id']) for e in ET.fromstring(raw).findall('.//p:cNvPr',NS))+1)
        geometry=re.search(r'<a:prstGeom\b.*?</a:prstGeom>',block,re.S)[0] if fit=='cover' else '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom>'
        picture='<p:pic><p:nvPicPr><p:cNvPr id="'+inserted+'" name="StratX image '+oid+'" descr='+quoteattr(alt)+'/><p:cNvPicPr><a:picLocks noChangeAspect="1"/></p:cNvPicPr><p:nvPr/></p:nvPicPr><p:blipFill>'+fragment+'</p:blipFill><p:spPr>'+transform+geometry+'<a:ln><a:noFill/></a:ln></p:spPr></p:pic>'
        block+=picture
    updated=raw[:found.start()]+block+raw[found.end():]
    removed=[]
    for placeholder in binding.get('remove_placeholder', []):
        pid=str(placeholder['shape_id'])
        if pid==oid or pid in removed or pid not in available:
            raise ValueError('Invalid placeholder removal target')
        p=available[pid]
        x,y,ww,hh=p['box_emu'];bx,by,bww,bhh=slot['box_emu']
        if p['kind']!='pic' or p['hidden'] or not (bx<=x and by<=y and x+ww<=bx+bww and y+hh<=by+bhh):
            raise ValueError('Only a picture placeholder wholly inside this image frame can be removed')
        if p['before_sha256']!=placeholder['before_sha256']:
            raise ValueError('Stale placeholder binding')
        match=next(m for sid,m,_ in object_blocks(updated) if sid==pid)
        updated=updated[:match.start()]+updated[match.end():]
        removed.append(pid)
    parts[part]=updated.encode()
    parts[rp]=relationships.encode();parts[media]=data
    ct=parts['[Content_Types].xml'].decode()
    if not re.search(r'<Default\b[^>]*Extension="png"',ct):
        ct=ct.replace('</Types>','<Default Extension="png" ContentType="image/png"/></Types>')
        parts['[Content_Types].xml']=ct.encode()
    return {'part':part,'shape_id':oid,'image_sha256':digest,'fit':fit,'pixel_size':[width,height],
            'original_box_emu':slot['box_emu'],'alt':alt,'removed_placeholder_ids':removed,
            'inserted_picture_id':inserted,'shared_source_media_overwritten':False}
