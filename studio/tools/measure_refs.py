"""Stage 2: measure the reference library + Morph templates -> references/measurements.json."""
import json, re, statistics as st, sys, zipfile, glob
from pathlib import Path

LIB = Path(sys.argv[1])            # slide-reference-library folder
PPTX = [Path(p) for p in sys.argv[2:]]   # native Morph examples (PELDA, SlidesCarnival)
OUT = Path(__file__).resolve().parents[1] / "references" / "measurements.json"


def q(v, p):
    v = sorted(v)
    return v[int(p * (len(v) - 1))]


res = {"decks": {}, "labels": {}, "morph_templates": {}}
labels = json.loads((LIB / "labels.json").read_text(encoding="utf-8"))
texts = {}
for f in sorted((LIB / "inspection").glob("*-text.json")):
    d = json.loads(f.read_text(encoding="utf-8"))
    ws = [len(re.findall(r"\w+", p["text"])) for p in d["pages"]]
    texts[d["id"]] = {p["page"]: p["text"] for p in d["pages"]}
    res["decks"][d["id"]] = {"genre": d.get("genre"), "slides": len(ws), "words_median": st.median(ws),
                             "words_p75": q(ws, .75), "words_p90": q(ws, .9)}
research = [w for k, v in res["decks"].items() if v["genre"] == "research" for w in [v["words_median"]]]
res["research_words_median_range"] = [min(research), max(research)]
by = {}
for l in labels:
    t = texts.get(l["deck_id"], {}).get(l["pdf_page"])
    w = len(re.findall(r"\w+", t)) if t else None
    by.setdefault(f'{l["quality"]}/{l["slide_type"]}', []).append({"slide": l["slide_id"], "words": w, "tags": l["tags"]})
res["labels"] = by
for p in PPTX:
    z = zipfile.ZipFile(p)
    durs, opts, objs, names = [], [], [], 0
    for n in z.namelist():
        if re.fullmatch(r"ppt/slides/slide\d+\.xml", n):
            s = z.read(n).decode("utf8", "ignore")
            durs += [int(x) for x in re.findall(r'p14:dur="(\d+)"', s)]
            opts += re.findall(r'morph option="(\w+)"', s)
            objs.append(len(re.findall(r"<p:cNvPr ", s)))
            names += len(re.findall(r'name="!!', s))
    res["morph_templates"][p.name] = {"slides": len(objs), "morph_durations_ms": sorted(set(durs)),
                                      "options": sorted(set(opts)), "objects_median": st.median(objs) if objs else None,
                                      "objects_max": max(objs) if objs else None, "bang_names": names}
OUT.parent.mkdir(exist_ok=True)
OUT.write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
print(json.dumps({k: v for k, v in res.items() if k != "labels"}, indent=1, ensure_ascii=False))
