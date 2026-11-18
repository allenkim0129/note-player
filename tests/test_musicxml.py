import io
import zipfile

import pytest
from defusedxml.common import DefusedXmlException

from app.musicxml import ScoreError, parse_score, read_musicxml


def score_xml(measures):
    return f"""<?xml version="1.0"?>
    <score-partwise version="4.0"><work><work-title>Test piece</work-title></work>
    <part-list><score-part id="P1"><part-name>Test</part-name></score-part></part-list>
    <part id="P1">{measures}</part></score-partwise>""".encode()


def note(step="C", octave=4, duration=4, extra="", alter=0):
    return f"""<note><pitch><step>{step}</step><alter>{alter}</alter><octave>{octave}</octave></pitch>
    <duration>{duration}</duration><voice>1</voice>{extra}</note>"""


ATTRIBUTES = """<attributes><divisions>4</divisions><key><fifths>1</fifths></key>
<time><beats>4</beats><beat-type>4</beat-type></time></attributes>"""


def test_accidentals_rests_dotted_tuplets_chords_and_tempo():
    xml = score_xml(f"""<measure number="1">{ATTRIBUTES}
    <direction><sound tempo="120"/><direction-type><dynamics><p/></dynamics></direction-type></direction>
    {note("F", duration=6, alter=1)}
    <note><rest/><duration>2</duration></note>
    {note("C", duration=4)}
    <note><chord/><pitch><step>E</step><octave>4</octave></pitch><duration>4</duration></note>
    {note("G", duration=4, extra="<notations><articulations><staccato/><accent/></articulations></notations>")}
    </measure><measure number="2"><direction><sound tempo="60"/></direction>
    {note("C", duration=4/3)}{note("D", duration=4/3)}{note("E", duration=4/3)}
    </measure>""")
    score = parse_score(xml)
    assert [n["midi"] for n in score["events"]] == [66, 60, 64, 67, 60, 62, 64]
    assert [n["time"] for n in score["events"][:4]] == [0, 1, 1, 1.5]
    assert score["events"][0]["duration"] == 0.75
    assert score["events"][3]["gate"] == 0.48
    assert score["events"][3]["velocity"] == pytest.approx(0.44 * 1.2)
    assert score["events"][-1]["rhythm"] == "1/3"
    assert score["events"][-1]["time"] == pytest.approx(2 + 2/3, abs=1e-5)
    assert score["duration"] == 3


def test_ties_across_measures_and_transposing_instrument():
    xml = score_xml(f"""<measure number="1">{ATTRIBUTES}
    <attributes><transpose><chromatic>-2</chromatic></transpose></attributes>
    {note(duration=16, extra='<tie type="start"/>')}</measure>
    <measure number="2">{note(duration=16, extra='<tie type="stop"/>')}</measure>""")
    score = parse_score(xml)
    assert len(score["events"]) == 1
    assert score["events"][0]["midi"] == 58
    assert score["events"][0]["rhythm"] == "8"
    assert score["events"][0]["duration"] == 4.8
    assert score["events"][0]["gate"] == 0.95
    assert any("100 BPM" in w for w in score["warnings"])


def test_simple_repeats_are_performed_but_voltas_are_explicit():
    xml = score_xml(f"""<measure number="1">{ATTRIBUTES}
    <barline location="left"><repeat direction="forward"/></barline>
    {note(duration=16)}<barline><repeat direction="backward"/></barline></measure>""")
    score = parse_score(xml)
    assert len(score["events"]) == 2
    assert score["duration"] == 4.8
    xml = xml.replace(b'<repeat direction="backward"/>', b'<ending number="1" type="start"/><repeat direction="backward"/>')
    score = parse_score(xml)
    assert len(score["events"]) == 1
    assert any("written order" in w for w in score["warnings"])


def test_multiple_voices_and_pickup_not_padded():
    xml = score_xml(f"""<measure number="0" implicit="yes">{ATTRIBUTES}
    {note("G", duration=4)}</measure>
    <measure number="1">{note(duration=16)}
    <backup><duration>16</duration></backup>{note("E", duration=16)}</measure>""")
    score = parse_score(xml)
    assert [n["time"] for n in score["events"]] == [0, 0.6, 0.6]
    assert score["duration"] == 3


def test_unsupported_symbols_are_reported_and_grace_is_not_faked():
    xml = score_xml(f"""<measure>{ATTRIBUTES}<direction><sound dacapo="yes"/>
    <direction-type><wedge type="crescendo"/></direction-type></direction>
    <note><grace/><pitch><step>D</step><octave>4</octave></pitch></note>
    {note(extra='<notations><ornaments><trill-mark/></ornaments><fermata/><technical><up-bow/></technical></notations>')}
    </measure>""")
    score = parse_score(xml)
    assert len(score["events"]) == 1
    for symbol in ["Grace", "Ornaments", "Fermatas", "hairpins", "D.C.", "Playing techniques"]:
        assert any(symbol in w for w in score["warnings"])


def test_no_notes_and_invalid_numbers_fail_explicitly():
    with pytest.raises(ScoreError, match="No playable"):
        parse_score(score_xml("<measure><note><rest/><duration>1</duration></note></measure>"))
    with pytest.raises(ScoreError, match="invalid number"):
        parse_score(score_xml(f'<measure><attributes><divisions>nan</divisions></attributes>{note()}</measure>'))


def test_compressed_musicxml_uses_container_without_extracting():
    data = io.BytesIO()
    xml = score_xml(f"<measure>{ATTRIBUTES}{note()}</measure>")
    with zipfile.ZipFile(data, "w") as archive:
        archive.writestr("META-INF/container.xml", '<container><rootfiles><rootfile full-path="score.musicxml"/></rootfiles></container>')
        archive.writestr("score.musicxml", xml)
    assert read_musicxml(data.getvalue()) == xml
    assert parse_score(data.getvalue())["events"][0]["midi"] == 60


def test_xml_entities_are_never_resolved():
    with pytest.raises(DefusedXmlException):
        parse_score(b'<!DOCTYPE score-partwise [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><score-partwise>&xxe;</score-partwise>')
