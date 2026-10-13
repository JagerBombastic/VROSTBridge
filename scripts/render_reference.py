"""Render docs/TECHNICAL_REFERENCE.md as a PDF (requires ReportLab; diagrams need mermaid-cli).

Usage: python3 scripts/render_reference.py /absolute/path/TECHNICAL_REFERENCE.pdf [--chrome PATH]

Documentation tooling only; never required by the running application. Mermaid diagrams are drawn with
mermaid-cli (`mmdc`) when it is on PATH; pass --chrome (or set PUPPETEER_EXECUTABLE_PATH) to use an
installed Chromium instead of one Puppeteer downloads. Without mmdc a diagram becomes a short note
pointing to the Markdown file, where GitHub draws it.
"""
from pathlib import Path
import argparse
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate, Frame, Image, KeepTogether, PageBreak, PageTemplate, Paragraph, Preformatted, Spacer, Table,
    TableStyle,
)
from reportlab.platypus.tableofcontents import TableOfContents

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'docs' / 'TECHNICAL_REFERENCE.md'
sys.path.insert(0, str(ROOT))
from launcher.version import CLIENT_VERSION, HOST_VERSION  # noqa: E402

FONT = Path('/usr/share/fonts/truetype/dejavu')
for name, file in [('Ref', 'DejaVuSans.ttf'), ('RefBold', 'DejaVuSans-Bold.ttf'), ('RefMono', 'DejaVuSansMono.ttf')]:
    pdfmetrics.registerFont(TTFont(name, str(FONT / file)))
pdfmetrics.registerFontFamily('Ref', normal='Ref', bold='RefBold', italic='Ref', boldItalic='RefBold')
INK = colors.HexColor('#152033')
MUTED = colors.HexColor('#526174')
BLUE = colors.HexColor('#15658c')
PALE = colors.HexColor('#edf4f8')
WIDTH, HEIGHT = A4
CONTENT = WIDTH - 100
STYLES = {
    'body': ParagraphStyle('body', fontName='Ref', fontSize=9.2, leading=13.4, textColor=INK, spaceAfter=6.5,
                           splitLongWords=True),
    'part': ParagraphStyle('part', fontName='RefBold', fontSize=22, leading=28, textColor=BLUE, spaceBefore=40,
                           spaceAfter=18, keepWithNext=True),
    'h1': ParagraphStyle('h1', fontName='RefBold', fontSize=15.5, leading=20, textColor=INK, spaceBefore=14,
                         spaceAfter=10, keepWithNext=True),
    'h2': ParagraphStyle('h2', fontName='RefBold', fontSize=11, leading=15.5, textColor=BLUE, spaceBefore=10,
                         spaceAfter=6, keepWithNext=True),
    'h3': ParagraphStyle('h3', fontName='RefBold', fontSize=9.6, leading=13.4, textColor=INK, spaceBefore=6,
                         spaceAfter=4, keepWithNext=True),
    'cell': ParagraphStyle('cell', fontName='Ref', fontSize=7.9, leading=11, textColor=INK, splitLongWords=True),
    'th': ParagraphStyle('th', fontName='RefBold', fontSize=7.9, leading=11, textColor=colors.white,
                         splitLongWords=True),
    'bullet': ParagraphStyle('bullet', fontName='Ref', fontSize=9.2, leading=13.4, textColor=INK, leftIndent=13,
                             firstLineIndent=-10, spaceAfter=4.5, splitLongWords=True),
    'bullet2': ParagraphStyle('bullet2', fontName='Ref', fontSize=9.0, leading=13, textColor=INK, leftIndent=27,
                              firstLineIndent=-10, spaceAfter=3.5, splitLongWords=True),
    'code': ParagraphStyle('code', fontName='RefMono', fontSize=7.6, leading=10, textColor=INK, backColor=PALE,
                           borderPadding=6, spaceBefore=4, spaceAfter=10),
    'note': ParagraphStyle('note', fontName='Ref', fontSize=8.6, leading=12, textColor=MUTED, spaceAfter=8),
}


def plain(text):
    return text.replace('‑', '-')


def inline(text):
    """Markdown inline syntax to ReportLab's paragraph markup (code spans kept literal)."""
    codes = []

    def keep(match):
        codes.append(match.group(1))
        return '\x00%d\x00' % (len(codes) - 1)

    text = re.sub(r'`([^`]*)`', keep, plain(text))
    text = html.escape(text, quote=False)
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'\1', text)  # links: their text
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'(?<![\w*])\*(?!\s)([^*]+?)(?<!\s)\*(?![\w*])', r'\1', text)  # no italic face: plain
    text = text.replace('\\|', '|')
    return re.sub(r'\x00(\d+)\x00', lambda m: '<font name="RefMono" size="7.9">' +
                  html.escape(codes[int(m.group(1))].replace('\\|', '|'), quote=False) + '</font>', text)


class Reference(BaseDocTemplate):
    def afterFlowable(self, flowable):
        key = getattr(flowable, 'outline_key', None)
        if key:
            level = flowable.outline_level
            self.canv.bookmarkPage(key)
            self.canv.addOutlineEntry(flowable.outline_title, key, level=level, closed=level > 0)
            self.notify('TOCEntry', (level, flowable.outline_title, self.page, key))


def page_chrome(revision):
    def chrome(canvas, doc):
        canvas.saveState()
        if doc.page > 1:
            canvas.setStrokeColor(colors.HexColor('#d6e0e8'))
            canvas.setLineWidth(.6)
            canvas.line(50, HEIGHT - 40, WIDTH - 50, HEIGHT - 40)
            canvas.setFont('RefBold', 8)
            canvas.setFillColor(INK)
            canvas.drawString(50, HEIGHT - 30, 'VROSTBRIDGE  /  TECHNICAL REFERENCE')
            canvas.setFont('Ref', 7.5)
            canvas.setFillColor(MUTED)
            canvas.drawRightString(WIDTH - 50, HEIGHT - 30, f'Host and client {HOST_VERSION}')
        canvas.setFont('Ref', 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawString(50, 29, f'Documentation {revision} / {HOST_VERSION}' if revision else f'VROSTBridge {HOST_VERSION}')
        canvas.drawRightString(WIDTH - 50, 29, str(doc.page))
        canvas.restoreState()
    return chrome


def chrome_path(explicit):
    for candidate in (explicit, os.environ.get('PUPPETEER_EXECUTABLE_PATH')):
        if candidate and Path(candidate).is_file():
            return candidate
    return None


def diagram(source, work, number, chrome):
    """A mermaid diagram as an Image flowable, or None when it cannot be drawn here."""
    mmdc = shutil.which('mmdc')
    if not mmdc:
        return None
    mmd = work / f'diagram{number}.mmd'
    png = work / f'diagram{number}.png'
    mmd.write_text(source, encoding='utf-8')
    command = [mmdc, '-i', str(mmd), '-o', str(png), '-s', '3', '-b', 'white', '-w', '1400']
    config = {'args': ['--no-sandbox', '--disable-gpu']}
    if chrome:
        config['executablePath'] = chrome
    (work / 'puppeteer.json').write_text(json.dumps(config), encoding='utf-8')
    command += ['-p', str(work / 'puppeteer.json')]
    result = subprocess.run(command, capture_output=True, text=True, timeout=180)
    if result.returncode != 0 or not png.is_file():
        print('diagram', number, 'not drawn:', (result.stderr or result.stdout)[-400:], file=sys.stderr)
        return None
    width, height = ImageReader(str(png)).getSize()
    scale = min(CONTENT / width, 420 / height)
    image = Image(str(png), width=width * scale, height=height * scale)
    image.hAlign = 'CENTER'
    return image


def column_widths(rows):
    """Every column at least as wide as its longest word; the rest shared by typical cell length."""
    count = max(len(row) for row in rows)
    minimum, weights = [], []
    for column in range(count):
        cells = [re.sub(r'[`*]|\[([^\]]+)\]\([^)]+\)', lambda m: m.group(1) or '', row[column])
                 for row in rows if column < len(row)]
        words = [word for cell in cells for word in re.split(r'[\s/]+', cell) if word]
        longest = max((pdfmetrics.stringWidth(word, 'RefBold', 7.9) for word in words), default=20)
        minimum.append(min(longest + 14, CONTENT * 0.4))
        lengths = sorted(len(cell) for cell in cells)
        typical = lengths[len(lengths) * 3 // 4] if lengths else 1
        weights.append(min(max(typical, 4), 90) ** 0.85)
    # Too wide together: narrow the widest columns first (their long words are code and may wrap).
    budget = CONTENT * 0.9
    while sum(minimum) > budget:
        widest = max(range(count), key=lambda n: minimum[n])
        others = sorted(minimum, reverse=True)
        target = max(others[1] if count > 1 else 0, 60, minimum[widest] - (sum(minimum) - budget))
        if target >= minimum[widest]:
            break
        minimum[widest] = target
    spare = CONTENT - sum(minimum)
    if spare <= 0:
        return [CONTENT * m / sum(minimum) for m in minimum]
    total = sum(weights)
    widths = [m + spare * w / total for m, w in zip(minimum, weights)]
    return widths


def table(rows):
    widths = column_widths(rows)
    data = [[Paragraph(inline(cell), STYLES['th' if n == 0 else 'cell']) for cell in row] for n, row in enumerate(rows)]
    flowable = Table(data, colWidths=widths, repeatRows=1, hAlign='LEFT', splitInRow=1)
    flowable.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), INK), ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, PALE]),
        ('LINEBELOW', (0, 1), (-1, -1), .3, colors.HexColor('#dce5eb')),
        ('LEFTPADDING', (0, 0), (-1, -1), 6), ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ('TOPPADDING', (0, 0), (-1, -1), 5), ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
    ]))
    return flowable


def heading(text, style, level, key):
    paragraph = Paragraph(inline(text), STYLES[style])
    paragraph.outline_title = re.sub(r'[`*]', '', plain(text))
    paragraph.outline_level = level
    paragraph.outline_key = key
    return paragraph


def introduction(lines):
    """The paragraph between the title and the contents list."""
    text = []
    for line in lines[1:]:
        if line.strip().startswith('**Contents**') or line.strip() == '---':
            break
        if line.strip():
            text.append(line.strip())
    return ' '.join(text)


def story_from(lines, work, chrome):
    story, diagrams, sections = [], 0, 0
    i = 0
    # The title, introduction and Markdown contents list are replaced by the cover and the PDF's own contents.
    while i < len(lines) and lines[i].strip() != '---':
        i += 1
    while i < len(lines):
        raw = lines[i]
        line = raw.strip()
        if not line or line == '---':
            i += 1
            continue
        if line.startswith('```'):
            language = line[3:].strip()
            block = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith('```'):
                block.append(lines[i])
                i += 1
            i += 1
            if language == 'mermaid':
                diagrams += 1
                image = diagram('\n'.join(block), work, diagrams, chrome)
                story.append(image if image else Paragraph(
                    'Diagram: drawn in the Markdown version of this document (docs/TECHNICAL_REFERENCE.md).',
                    STYLES['note']))
                story.append(Spacer(1, 8))
            else:
                story.append(Preformatted('\n'.join(block), STYLES['code']))
            continue
        if line.startswith('# '):
            title = line[2:]
            story += [PageBreak(), heading(title, 'part', 0, f'part-{len(story)}')]
            i += 1
        elif line.startswith('## '):
            sections += 1
            story.append(heading(line[3:], 'h1', 1, f'section-{sections}'))
            i += 1
        elif line.startswith('### '):
            story.append(Paragraph(inline(line[4:]), STYLES['h2']))
            i += 1
        elif line.startswith('#### '):
            story.append(Paragraph(inline(line[5:]), STYLES['h3']))
            i += 1
        elif line.startswith('|'):
            rows = []
            while i < len(lines) and lines[i].strip().startswith('|'):
                cells = [cell.strip() for cell in re.split(r'(?<!\\)\|', lines[i].strip().strip('|'))]
                if not all(re.fullmatch(r'[:\- ]+', cell) for cell in cells):
                    rows.append(cells)
                i += 1
            story += [table(rows), Spacer(1, 10)]
        elif re.match(r'^(\s*)([-*]|\d+\.) ', raw):
            indent = len(raw) - len(raw.lstrip())
            marker, text = re.match(r'^\s*([-*]|\d+\.) (.*)$', raw).groups()
            i += 1
            while i < len(lines) and lines[i].strip() and lines[i].startswith(' ' * (indent + 2)) \
                    and not re.match(r'^\s*([-*]|\d+\.) ', lines[i]):
                text += ' ' + lines[i].strip()
                i += 1
            bullet = '– ' if indent >= 2 and marker in '-*' else ('- ' if marker in '-*' else marker + ' ')
            story.append(Paragraph(bullet + inline(text), STYLES['bullet2' if indent >= 2 else 'bullet']))
        elif line.startswith('>'):
            quote = []
            while i < len(lines) and lines[i].strip().startswith('>'):
                quote.append(lines[i].strip()[1:].strip())
                i += 1
            story.append(Paragraph(inline(' '.join(quote)), STYLES['note']))
        else:
            paragraph = [line]
            i += 1
            while i < len(lines) and lines[i].strip() and not lines[i].lstrip().startswith(('#', '|', '```', '>')) \
                    and not re.match(r'^\s*([-*]|\d+\.) ', lines[i]) and lines[i].strip() != '---':
                paragraph.append(lines[i].strip())
                i += 1
            story.append(Paragraph(inline(' '.join(paragraph)), STYLES['body']))
    return story


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('output')
    parser.add_argument('--chrome', help='Chromium or Chrome for mermaid-cli')
    args = parser.parse_args()
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    index = (ROOT / 'docs' / 'DOCUMENTATION_INDEX.md').read_text(encoding='utf-8')
    found = re.search(r'Documentation revision \*\*(\d{4}-\d{2}-\d{2}) / ' + re.escape(HOST_VERSION) + r'\*\*', index)
    revision = found.group(1) if found else ''
    lines = SOURCE.read_text(encoding='utf-8').splitlines()
    doc = Reference(str(output), pagesize=A4, title='VROSTBridge - Technical Reference', author='JagerBombastic',
                    subject=f'VROSTBridge host, Bazzite client and Windows client {HOST_VERSION}: how it works, what it needs, '
                            'and where it differs from ALVR, Baballonia and SteamVR',
                    leftMargin=50, rightMargin=50, topMargin=59, bottomMargin=49)
    frame = Frame(50, 49, CONTENT, HEIGHT - 108, leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    doc.addPageTemplates(PageTemplate(id='body', frames=[frame], onPage=page_chrome(revision)))
    mark = ROOT / 'brand/assets/logo/vrostbridge-mark.png'
    mark_width, mark_height = ImageReader(str(mark)).getSize()
    logo = Image(str(mark), width=110, height=110 * mark_height / mark_width)
    logo.hAlign = 'LEFT'
    cover = ParagraphStyle('cover', fontName='RefBold', fontSize=34, leading=40, textColor=INK, spaceAfter=12)
    subtitle = ParagraphStyle('subtitle', fontName='Ref', fontSize=21, leading=29, textColor=BLUE, spaceAfter=26)
    story = [Spacer(1, 56), logo, Spacer(1, 24), Paragraph('VROSTBridge', cover),
             Paragraph('Technical reference', subtitle),
             Paragraph(f'Windows rendering host <b>{HOST_VERSION}</b><br/>Bazzite and Windows headset clients <b>{CLIENT_VERSION}</b>'
                       '<br/>MIT licence, by JagerBombastic', STYLES['body']),
             Spacer(1, 16), Paragraph(inline(introduction(lines)), STYLES['body']), Spacer(1, 10),
             Paragraph('Canonical text: docs/TECHNICAL_REFERENCE.md in the source. The PDF presents it with a cover, '
                       'contents and drawn diagrams; where the two differ, the Markdown is current.', STYLES['note']),
             PageBreak(), Paragraph('Contents', STYLES['h1'])]
    toc = TableOfContents()
    toc.levelStyles = [
        ParagraphStyle('toc0', fontName='RefBold', fontSize=10.5, leading=15, textColor=BLUE, spaceBefore=8),
        ParagraphStyle('toc1', fontName='Ref', fontSize=9.2, leading=13, textColor=INK, leftIndent=14,
                       rightIndent=20),
    ]
    story.append(toc)
    with tempfile.TemporaryDirectory(prefix='vrostbridge-reference-') as work:
        story += story_from(lines, Path(work), chrome_path(args.chrome))
        doc.multiBuild(story)
    print(output)


if __name__ == '__main__':
    main()
