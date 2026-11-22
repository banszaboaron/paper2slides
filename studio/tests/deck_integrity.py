"""Structural checks on a built deck (XML order, Morph blocks, build ids, unique names)."""
import re, sys, zipfile
from lxml import etree
from pptx import Presentation

path = sys.argv[1]
z = zipfile.ZipFile(path)
P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
problems = []
slides = sorted([n for n in z.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)], key=lambda n: int(re.findall(r"\d+", n)[0]))
for n in slides:
    root = etree.fromstring(z.read(n))
    tags = [c.tag.split("}")[1] for c in root]
    order = [t for t in tags if t in ("cSld", "clrMapOvr", "AlternateContent", "transition", "timing")]
    if order != sorted(order, key=["cSld", "clrMapOvr", "AlternateContent", "transition", "timing"].index):
        problems.append(f"{n}: child order {order}")
    ids = [int(x) for x in root.xpath("//p:cNvPr/@id", namespaces={"p": P[1:-1]})]
    names = root.xpath("//p:cNvPr/@name", namespaces={"p": P[1:-1]})
    bang = [x for x in names if x.startswith("!!")]
    if len(bang) != len(set(bang)):
        problems.append(f"{n}: duplicate !! names")
    for spid in root.xpath("//p:spTgt/@spid", namespaces={"p": P[1:-1]}):
        if int(spid) not in ids:
            problems.append(f"{n}: animation targets missing shape {spid}")
    ctn = [int(x) for x in root.xpath("//p:cTn/@id", namespaces={"p": P[1:-1]})]
    if len(ctn) != len(set(ctn)):
        problems.append(f"{n}: duplicate cTn ids")
Presentation(path)
print(f"{len(slides)} slides checked;", "OK" if not problems else "\n".join(problems))
