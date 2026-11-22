"""Transition and animation XML, copied from native PowerPoint files.

The Morph block is byte-for-byte the structure found in PELDA.pptx and in the
SlidesCarnival Morph templates (p159:morph inside mc:AlternateContent with a fade
fallback, placed on the *target* slide). The click-build is the standard Fade
entrance (presetID 10) in the same tree shape PowerPoint writes.
"""
from __future__ import annotations

from lxml import etree

P = "http://schemas.openxmlformats.org/presentationml/2006/main"
MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"

MORPH_XML = (
    '<mc:AlternateContent xmlns:mc="{mc}" xmlns:p="{p}">'
    '<mc:Choice xmlns:p159="http://schemas.microsoft.com/office/powerpoint/2015/09/main" Requires="p159">'
    '<p:transition spd="slow" xmlns:p14="http://schemas.microsoft.com/office/powerpoint/2010/main" p14:dur="{dur}">'
    '<p159:morph option="{opt}"/></p:transition></mc:Choice>'
    '<mc:Fallback><p:transition spd="slow"><p:fade/></p:transition></mc:Fallback></mc:AlternateContent>'
)

FADE_XML = (
    '<mc:AlternateContent xmlns:mc="{mc}" xmlns:p="{p}">'
    '<mc:Choice xmlns:p14="http://schemas.microsoft.com/office/powerpoint/2010/main" Requires="p14">'
    '<p:transition spd="med" p14:dur="{dur}"><p:fade/></p:transition></mc:Choice>'
    '<mc:Fallback><p:transition spd="med"><p:fade/></p:transition></mc:Fallback></mc:AlternateContent>'
)


def _insert_after_clrmap(sld, node):
    clr = sld.find(f"{{{P}}}clrMapOvr")
    idx = list(sld).index(clr) + 1 if clr is not None else len(sld)
    for tag in ("transition", "timing"):
        for old in sld.findall(f"{{{P}}}{tag}"):
            sld.remove(old)
    for old in sld.findall(f"{{{MC}}}AlternateContent"):
        sld.remove(old)
    sld.insert(idx, node)


def set_transition(slide, kind: str, dur_ms: int = 1200, option: str = "byObject"):
    if kind == "none":
        return
    tpl = MORPH_XML if kind == "morph" else FADE_XML
    node = etree.fromstring(tpl.format(mc=MC, p=P, dur=dur_ms, opt=option))
    _insert_after_clrmap(slide._element, node)


def _fade_par(ctn: list[int], spid: int, dur: int, click: bool) -> str:
    def nid():
        ctn[0] += 1
        return ctn[0]
    a, b, c, d, e = nid(), nid(), nid(), nid(), nid()
    node_type = "clickEffect" if click else "withEffect"
    return (
        f'<p:par><p:cTn id="{a}" fill="hold"><p:stCondLst><p:cond delay="indefinite"/></p:stCondLst><p:childTnLst>'
        f'<p:par><p:cTn id="{b}" fill="hold"><p:stCondLst><p:cond delay="0"/></p:stCondLst><p:childTnLst>'
        f'<p:par><p:cTn id="{c}" presetID="10" presetClass="entr" presetSubtype="0" fill="hold" grpId="0" nodeType="{node_type}">'
        f'<p:stCondLst><p:cond delay="0"/></p:stCondLst><p:childTnLst>'
        f'<p:set><p:cBhvr><p:cTn id="{d}" dur="1" fill="hold"><p:stCondLst><p:cond delay="0"/></p:stCondLst></p:cTn>'
        f'<p:tgtEl><p:spTgt spid="{spid}"/></p:tgtEl><p:attrNameLst><p:attrName>style.visibility</p:attrName></p:attrNameLst></p:cBhvr>'
        f'<p:to><p:strVal val="visible"/></p:to></p:set>'
        f'<p:animEffect transition="in" filter="fade"><p:cBhvr><p:cTn id="{e}" dur="{dur}"/>'
        f'<p:tgtEl><p:spTgt spid="{spid}"/></p:tgtEl></p:cBhvr></p:animEffect>'
        f'</p:childTnLst></p:cTn></p:par></p:childTnLst></p:cTn></p:par></p:childTnLst></p:cTn></p:par>'
    )


def set_builds(slide, steps: list[list[tuple[int, bool]]], dur_ms: int = 450):
    """steps: one entry per click, each a list of (shape_id, is_text_shape).

    All shapes of one step appear together on that click (first clickEffect, the
    rest are merged into the same click group).
    """
    if not steps:
        return
    ctn = [2]
    pars = []
    bld = []
    for step in steps:
        inner = []
        for k, (spid, is_sp) in enumerate(step):
            inner.append((spid, k == 0))
            if is_sp:
                bld.append(spid)
        # one click-group per step: first shape clickEffect, rest withEffect inside the same outer par
        first = _fade_par(ctn, inner[0][0], dur_ms, True)
        if len(inner) > 1:
            # withEffect siblings go into the same timing group as the clickEffect
            extra_inner = _with_effect_inner(ctn, [s for s, _ in inner[1:]], dur_ms)
            tail = "</p:childTnLst></p:cTn></p:par></p:childTnLst></p:cTn></p:par></p:childTnLst></p:cTn></p:par>"
            assert first.endswith(tail)
            first = first[: -len(tail)] + "</p:childTnLst></p:cTn></p:par>" + extra_inner + \
                "</p:childTnLst></p:cTn></p:par></p:childTnLst></p:cTn></p:par>"
        pars.append(first)
    bld_xml = "".join(f'<p:bldP spid="{s}" grpId="0"/>' for s in dict.fromkeys(bld))
    xml = (
        f'<p:timing xmlns:p="{P}"><p:tnLst><p:par><p:cTn id="1" dur="indefinite" restart="never" nodeType="tmRoot"><p:childTnLst>'
        f'<p:seq concurrent="1" nextAc="seek"><p:cTn id="2" dur="indefinite" nodeType="mainSeq"><p:childTnLst>'
        + "".join(pars) +
        '</p:childTnLst></p:cTn><p:prevCondLst><p:cond evt="onPrev" delay="0"><p:tgtEl><p:sldTgt/></p:tgtEl></p:cond></p:prevCondLst>'
        '<p:nextCondLst><p:cond evt="onNext" delay="0"><p:tgtEl><p:sldTgt/></p:tgtEl></p:cond></p:nextCondLst></p:seq>'
        '</p:childTnLst></p:cTn></p:par></p:tnLst>'
        + (f"<p:bldLst>{bld_xml}</p:bldLst>" if bld_xml else "") + "</p:timing>"
    )
    node = etree.fromstring(xml)
    sld = slide._element
    for old in sld.findall(f"{{{P}}}timing"):
        sld.remove(old)
    sld.append(node)  # timing must be after transition


def _with_effect_inner(ctn, spids, dur):
    out = []
    for spid in spids:
        ctn[0] += 1
        c = ctn[0]
        ctn[0] += 1
        d = ctn[0]
        ctn[0] += 1
        e = ctn[0]
        out.append(
            f'<p:par><p:cTn id="{c}" presetID="10" presetClass="entr" presetSubtype="0" fill="hold" grpId="0" nodeType="withEffect">'
            f'<p:stCondLst><p:cond delay="0"/></p:stCondLst><p:childTnLst>'
            f'<p:set><p:cBhvr><p:cTn id="{d}" dur="1" fill="hold"><p:stCondLst><p:cond delay="0"/></p:stCondLst></p:cTn>'
            f'<p:tgtEl><p:spTgt spid="{spid}"/></p:tgtEl><p:attrNameLst><p:attrName>style.visibility</p:attrName></p:attrNameLst></p:cBhvr>'
            f'<p:to><p:strVal val="visible"/></p:to></p:set>'
            f'<p:animEffect transition="in" filter="fade"><p:cBhvr><p:cTn id="{e}" dur="{dur}"/>'
            f'<p:tgtEl><p:spTgt spid="{spid}"/></p:tgtEl></p:cBhvr></p:animEffect>'
            f'</p:childTnLst></p:cTn></p:par>')
    return "".join(out)


def set_autoplay(slide, spid: int, dur_ms: int = 600000):
    """The video on this slide starts by itself when the slide appears (PowerPoint 'Start: Automatically');
    the next click leaves the slide."""
    xml = (
        f'<p:timing xmlns:p="{P}"><p:tnLst><p:par><p:cTn id="1" dur="indefinite" restart="never" nodeType="tmRoot"><p:childTnLst>'
        f'<p:seq concurrent="1" nextAc="seek"><p:cTn id="2" dur="indefinite" nodeType="mainSeq"><p:childTnLst>'
        f'<p:par><p:cTn id="3" fill="hold"><p:stCondLst><p:cond delay="indefinite"/><p:cond evt="onBegin" delay="0"><p:tn val="2"/></p:cond></p:stCondLst><p:childTnLst>'
        f'<p:par><p:cTn id="4" fill="hold"><p:stCondLst><p:cond delay="0"/></p:stCondLst><p:childTnLst>'
        f'<p:par><p:cTn id="5" presetID="1" presetClass="mediacall" presetSubtype="0" fill="hold" nodeType="afterEffect">'
        f'<p:stCondLst><p:cond delay="0"/></p:stCondLst><p:childTnLst>'
        f'<p:cmd type="call" cmd="playFrom(0.0)"><p:cBhvr><p:cTn id="6" dur="{dur_ms}" fill="hold"/>'
        f'<p:tgtEl><p:spTgt spid="{spid}"/></p:tgtEl></p:cBhvr></p:cmd>'
        f'</p:childTnLst></p:cTn></p:par></p:childTnLst></p:cTn></p:par></p:childTnLst></p:cTn></p:par>'
        f'</p:childTnLst></p:cTn><p:prevCondLst><p:cond evt="onPrev" delay="0"><p:tgtEl><p:sldTgt/></p:tgtEl></p:cond></p:prevCondLst>'
        f'<p:nextCondLst><p:cond evt="onNext" delay="0"><p:tgtEl><p:sldTgt/></p:tgtEl></p:cond></p:nextCondLst></p:seq>'
        f'<p:video><p:cMediaNode vol="80000"><p:cTn id="7" fill="hold" display="0"><p:stCondLst><p:cond delay="indefinite"/></p:stCondLst>'
        f'<p:endCondLst><p:cond evt="onStopAudio" delay="0"><p:tgtEl><p:sldTgt/></p:tgtEl></p:cond></p:endCondLst></p:cTn>'
        f'<p:tgtEl><p:spTgt spid="{spid}"/></p:tgtEl></p:cMediaNode></p:video>'
        f'</p:childTnLst></p:cTn></p:par></p:tnLst></p:timing>'
    )
    node = etree.fromstring(xml)
    sld = slide._element
    for old in sld.findall(f"{{{P}}}timing"):
        sld.remove(old)
    sld.append(node)
