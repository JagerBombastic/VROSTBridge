"""Render the canonical project specification (requires ReportLab).

Usage: python3 scripts/render_spec.py /absolute/path/specification.pdf
Documentation tooling only; never required by the running application.
"""
from pathlib import Path
import html
import re
import sys
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate, Frame, PageTemplate, Paragraph, Spacer, PageBreak,
    Table, TableStyle, Image, KeepTogether,
)
from reportlab.platypus.tableofcontents import TableOfContents

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = Path(sys.argv[1]).resolve()
OUTPUT.parent.mkdir(parents=True, exist_ok=True)
FONT = Path('/usr/share/fonts/truetype/dejavu')
for name, file in [('Spec', 'DejaVuSans.ttf'), ('SpecBold', 'DejaVuSans-Bold.ttf'),
                   ('SpecMono', 'DejaVuSansMono.ttf')]:
    pdfmetrics.registerFont(TTFont(name, str(FONT / file)))
pdfmetrics.registerFontFamily('Spec', normal='Spec', bold='SpecBold', italic='Spec', boldItalic='SpecBold')
INK = colors.HexColor('#152033')
MUTED = colors.HexColor('#526174')
BLUE = colors.HexColor('#15658c')
PALE = colors.HexColor('#edf4f8')
WIDTH, HEIGHT = A4
CONTENT = WIDTH - 100
styles = {
    'body': ParagraphStyle('body', fontName='Spec', fontSize=9.4, leading=13.6,
                           textColor=INK, spaceAfter=7, splitLongWords=True),
    'h1': ParagraphStyle('h1', fontName='SpecBold', fontSize=17, leading=22,
                         textColor=INK, spaceBefore=16, spaceAfter=12, keepWithNext=True),
    'h2': ParagraphStyle('h2', fontName='SpecBold', fontSize=11.1, leading=16,
                         textColor=BLUE, spaceBefore=12, spaceAfter=7, keepWithNext=True),
    'cell': ParagraphStyle('cell', fontName='Spec', fontSize=8.1, leading=11.5,
                           textColor=INK, splitLongWords=True),
    'th': ParagraphStyle('th', fontName='SpecBold', fontSize=8.1, leading=11.5,
                         textColor=colors.white, splitLongWords=True),
    'bullet': ParagraphStyle('bullet', fontName='Spec', fontSize=9.4, leading=13.6,
                             textColor=INK, leftIndent=13, firstLineIndent=-10,
                             spaceAfter=6, splitLongWords=True),
    # A list item nested under another (two or more leading spaces in the Markdown).
    'bullet2': ParagraphStyle('bullet2', fontName='Spec', fontSize=9.2, leading=13.2,
                              textColor=INK, leftIndent=27, firstLineIndent=-10,
                              spaceAfter=4, splitLongWords=True),
}

def normal(text):
    return text.replace('\u2011', '-').replace('\u2013', '-').replace('\u2014', '-')

def inline(text):
    text = html.escape(normal(text))
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    parts = text.split('`')
    for n in range(0, len(parts), 2):  # outside code spans only
        # *emphasis* (the font family has no italic face): the words themselves, without the asterisks.
        parts[n] = re.sub(r'(?<![\w*])\*(?!\s)([^*]+?)(?<!\s)\*(?![\w*])', r'\1', parts[n])
    for n in range(1, len(parts), 2):
        parts[n] = '<font name="SpecMono" size="8.2">' + parts[n] + '</font>'
    text = ''.join(parts)
    # The canonical spec uses named sibling files; don't create broken sandbox URLs.
    text = re.sub(r'\[([^]]+)\]\(([^)]+)\)', r'\1', text)
    return text

class SpecDoc(BaseDocTemplate):
    def afterFlowable(self, flowable):
        if isinstance(flowable, Paragraph) and hasattr(flowable, 'section_title'):
            label, key = flowable.section_title, flowable.section_key
            self.canv.bookmarkPage(key)
            self.canv.addOutlineEntry(label, key, level=0, closed=False)
            self.notify('TOCEntry', (0, label, self.page, key))

def chrome(canvas, doc):
    canvas.saveState()
    if doc.page > 1:
        canvas.setStrokeColor(colors.HexColor('#d6e0e8'))
        canvas.setLineWidth(.6)
        canvas.line(50, HEIGHT-40, WIDTH-50, HEIGHT-40)
        canvas.setFont('SpecBold', 8)
        canvas.setFillColor(INK)
        canvas.drawString(50, HEIGHT-30, 'VROSTBRIDGE  /  PROJECT SPECIFICATION')
        canvas.setFont('Spec', 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawRightString(WIDTH-50, HEIGHT-30, 'Host and client 1.0.0.14')
    canvas.setFont('Spec', 7.5)
    canvas.setFillColor(MUTED)
    canvas.drawString(50, 29, 'Documentation 2026-10-18 / 1.0.0.14')
    canvas.drawRightString(WIDTH-50, 29, f'{doc.page}')
    canvas.restoreState()

doc = SpecDoc(str(OUTPUT), pagesize=A4, title='VROSTBridge - Full Project Specification',
              author='JagerBombastic', subject='VROSTBridge host, Bazzite client and Windows client 1.0.0.14; documentation 2026-10-18',
              leftMargin=50, rightMargin=50, topMargin=59, bottomMargin=49)
frame = Frame(50, 49, CONTENT, HEIGHT-108, leftPadding=0, rightPadding=0,
              topPadding=0, bottomPadding=0)
doc.addPageTemplates(PageTemplate(id='body', frames=[frame], onPage=chrome))
story = [Spacer(1, 56)]
MARK = ROOT/'brand/assets/logo/vrostbridge-mark.png'  # the symbol, no text
from reportlab.lib.utils import ImageReader  # noqa: E402
mark_width, mark_height = ImageReader(str(MARK)).getSize()
logo = Image(str(MARK), width=110, height=110 * mark_height / mark_width)
logo.hAlign = 'LEFT'
story += [logo, Spacer(1, 24)]
cover_title = ParagraphStyle('cover', fontName='SpecBold', fontSize=34, leading=40, textColor=INK, spaceAfter=12)
subtitle = ParagraphStyle('subtitle', fontName='Spec', fontSize=21, leading=29, textColor=BLUE, spaceAfter=26)
story += [Paragraph('VROSTBridge', cover_title), Paragraph('Full project specification', subtitle)]
story += [Paragraph('An open-source remote PCVR bridge<br/><i>VR Rendering OutSide The Box – Bridge</i><br/>Windows rendering host <b>1.0.0.14</b><br/>Bazzite headset client <b>1.0.0.14</b><br/>Windows headset client <b>1.0.0.14</b><br/>MIT licence, by JagerBombastic', styles['body']), Spacer(1, 22)]
box = Table([[Paragraph('<b>Current handoff</b><br/>Architecture, requirements, implementation boundaries, build and install contracts, and release acceptance for 1.0.0.14: the current design and the reasons for it. Recent changes are in the release notes', styles['body'])]], colWidths=[CONTENT])
box.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),PALE),('BOX',(0,0),(-1,-1),.5,colors.HexColor('#c4d8e5')),('LEFTPADDING',(0,0),(-1,-1),16),('RIGHTPADDING',(0,0),(-1,-1),16),('TOPPADDING',(0,0),(-1,-1),14),('BOTTOMPADDING',(0,0),(-1,-1),9)]))
story += [box, Spacer(1, 22), Paragraph('<b>Validation boundary</b><br/>Index and Beyond 2e streaming, tracking and game audio are confirmed on hardware through the bridge, with eye-tracked foveation at the headset\'s full resolution, calibration over the stream in Desktop Mode, packet repair and the menu button. The other features are contract-tested and listed as pending in section 16.2. The Windows client is built and tested in code only and has not yet run on Windows hardware; Game Mode sessions, calibrated gaze in Game Mode and the Beyond projection check are the remaining gates.', styles['body']), PageBreak()]
story += [Paragraph('Contents', styles['h1'])]
toc = TableOfContents()
toc.levelStyles = [ParagraphStyle('toc', fontName='Spec', fontSize=10, leading=15,
                                  textColor=INK, spaceBefore=6, leftIndent=0,
                                  firstLineIndent=0, rightIndent=20)]
story += [toc, Spacer(1, 20), Paragraph('Canonical text: docs/PROJECT_SPEC.md in the source archive. The PDF presents that specification with a cover and navigation; dated release reports remain in docs/evidence/.', styles['body']), PageBreak()]

lines = (ROOT/'docs/PROJECT_SPEC.md').read_text().splitlines()
i = 1  # title already represented on cover
while i < len(lines):
    line = lines[i].strip()
    if not line:
        i += 1
        continue
    if line.startswith('## '):
        title = normal(line[3:])
        p = Paragraph(inline(title), styles['h1'])
        p.section_title = title
        p.section_key = 'section-' + title.split('.')[0]
        story.append(p); i += 1
    elif line.startswith('### '):
        story.append(Paragraph(inline(line[4:]), styles['h2'])); i += 1
    elif line.startswith('#### '):
        story.append(Paragraph('<b>' + inline(line[5:]) + '</b>', styles['body'])); i += 1
    elif line.startswith('|'):
        rows = []
        while i < len(lines) and lines[i].strip().startswith('|'):
            cells = [x.strip() for x in lines[i].strip().strip('|').split('|')]
            if not all(re.fullmatch(r'[:\- ]+', x) for x in cells): rows.append(cells)
            i += 1
        count = len(rows[0])
        if count == 2:
            widths = [CONTENT*.32, CONTENT*.68]
        elif rows[0][0] == 'ID':
            widths = [CONTENT*.105, CONTENT*.535, CONTENT*.36]
        elif rows[0][0] == 'Area':  # the release baseline: long current-state cells (1.0.0.4, 1.0.0.5)
            widths = [CONTENT*.15, CONTENT*.55, CONTENT*.30]
        else:
            widths = [CONTENT*.26, CONTENT*.37, CONTENT*.37]
        data = [[Paragraph(inline(cell), styles['th' if n == 0 else 'cell']) for cell in row] for n, row in enumerate(rows)]
        table = Table(data, colWidths=widths, repeatRows=1, hAlign='LEFT', splitInRow=1)  # a row taller than a page splits
        table.setStyle(TableStyle([
            ('BACKGROUND',(0,0),(-1,0),INK),('VALIGN',(0,0),(-1,-1),'TOP'),
            ('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white, PALE]),
            ('LINEBELOW',(0,0),(-1,0),.4,INK),
            ('LINEBELOW',(0,1),(-1,-1),.3,colors.HexColor('#dce5eb')),
            ('LEFTPADDING',(0,0),(-1,-1),8),('RIGHTPADDING',(0,0),(-1,-1),8),
            ('TOPPADDING',(0,0),(-1,-1),6.5),('BOTTOMPADDING',(0,0),(-1,-1),6.5),
        ]))
        story += [table, Spacer(1, 12)]
    elif line.startswith('- ') or re.match(r'^\d+\. ', line):
        nested = lines[i].startswith('  ')
        content = (('\u2013 ' if nested else '- ') + inline(line[2:])) if line.startswith('- ') else inline(line)
        story.append(Paragraph(content, styles['bullet2' if nested else 'bullet'])); i += 1
    else:
        para = [line]; i += 1
        while i < len(lines) and lines[i].strip() and not lines[i].startswith(('#','|','- ')) and not re.match(r'^\d+\. ',lines[i]):
            para.append(lines[i].strip()); i += 1
        story.append(Paragraph(inline(' '.join(para)), styles['body']))
doc.multiBuild(story)
print(OUTPUT)
