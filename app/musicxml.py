"""Turn Audiveris MusicXML into a bounded, browser-playable performance."""

import bisect
import io
import math
import zipfile
from fractions import Fraction

from defusedxml import ElementTree as ET

MAX_XML_BYTES = 8 * 1024 * 1024
DYNAMICS = {"ppp": 0.22, "pp": 0.32, "p": 0.44, "mp": 0.56,
            "mf": 0.68, "f": 0.8, "ff": 0.9, "fff": 1.0}
STEPS = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


class ScoreError(ValueError):
    pass


def read_musicxml(data: bytes) -> bytes:
    if data.startswith(b"PK"):
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if len(archive.infolist()) > 100:
                raise ScoreError("The exported score contains too many files.")
            name = None
            if "META-INF/container.xml" in archive.namelist():
                info = archive.getinfo("META-INF/container.xml")
                if info.file_size > 64_000:
                    raise ScoreError("Invalid MusicXML container.")
                container = ET.fromstring(archive.read(info))
                for node in container.iter():
                    if node.tag.rsplit("}", 1)[-1] == "rootfile":
                        name = node.get("full-path")
                        break
            if not name:
                name = next((n for n in archive.namelist()
                             if n.endswith((".xml", ".musicxml")) and not n.startswith("META-INF/")), None)
            if not name or archive.getinfo(name).file_size > MAX_XML_BYTES:
                raise ScoreError("No readable MusicXML score was exported.")
            return archive.read(name)
    if len(data) > MAX_XML_BYTES:
        raise ScoreError("The exported score is too large.")
    return data


def number(text, default=0.0):
    value = float(text) if text is not None else default
    if not math.isfinite(value):
        raise ScoreError("The score contains an invalid number.")
    return value


def expanded_measures(part, warn):
    measures = list(part.findall("measure"))
    if any(m.find(".//ending") is not None for m in measures):
        warn("Repeat endings (voltas) are not supported; this part plays in written order.")
        return measures
    result, index, start, passes = [], 0, 0, {}
    while index < len(measures):
        measure = measures[index]
        repeats = measure.findall("barline/repeat")
        if any(r.get("direction") == "forward" for r in repeats):
            start = index
        result.append(measure)
        backwards = next((r for r in repeats if r.get("direction") == "backward"), None)
        if backwards is not None:
            times = int(backwards.get("times", "2"))
            if not 1 <= times <= 4:
                warn("Repeat counts above four are limited to four passes.")
                times = max(1, min(4, times))
            passes[index] = passes.get(index, 1)
            if passes[index] < times:
                passes[index] += 1
                index = start
                continue
            start = index + 1
        index += 1
        if len(result) > 2000:
            raise ScoreError("This score has too many repeated measures.")
    return result


def parse_score(data: bytes) -> dict:
    root = ET.fromstring(read_musicxml(data))
    for node in root.iter():
        node.tag = node.tag.rsplit("}", 1)[-1]
    if root.tag != "score-partwise":
        raise ScoreError("Only partwise MusicXML scores are supported.")
    warnings = [
        "Optical recognition can miss or misread notes and symbols. Compare the detected notes with your photo before playing."
    ]

    def warn(message):
        if message not in warnings:
            warnings.append(message)

    events, tempos, parts, signatures = [], {}, [], []
    total_beats = 0.0
    measure_count = 0
    names = {p.get("id"): p.findtext("part-name", "Part") for p in root.findall("part-list/score-part")}
    for part in root.findall("part"):
        part_id = part.get("id", "P1")
        parts.append(names.get(part_id, part_id))
        divisions, transpose, velocity, base, meter = 1.0, 0, 0.68, 0.0, 4.0
        ties = {}
        measures = expanded_measures(part, warn)
        measure_count = max(measure_count, len(measures))
        for measure_index, measure in enumerate(measures):
            cursor, furthest, previous_start = 0.0, 0.0, 0.0
            for item in measure:
                if item.tag == "attributes":
                    divisions = number(item.findtext("divisions"), divisions)
                    if divisions <= 0:
                        raise ScoreError("The score has invalid timing divisions.")
                    time = item.find("time")
                    if time is not None:
                        beats, beat_type = time.findtext("beats"), time.findtext("beat-type")
                        if beats and beat_type:
                            meter = sum(number(b) for b in beats.split("+")) * 4 / number(beat_type)
                            signature = f"{beats}/{beat_type}"
                            if signature not in signatures:
                                signatures.append(signature)
                    trans = item.find("transpose")
                    if trans is not None:
                        transpose = int(trans.findtext("chromatic", "0")) + 12 * int(trans.findtext("octave-change", "0"))
                    if item.find("key/key-step") is not None:
                        warn("Nonstandard key signatures may not play correctly.")
                elif item.tag in ("backup", "forward"):
                    delta = number(item.findtext("duration")) / divisions
                    cursor += -delta if item.tag == "backup" else delta
                    if cursor < -0.001:
                        raise ScoreError("The recognized voices have inconsistent timing. Try a clearer photo.")
                    cursor = max(0.0, cursor)
                    furthest = max(furthest, cursor)
                elif item.tag == "direction":
                    when = base + cursor + number(item.findtext("offset")) / divisions
                    sound = item.find("sound")
                    if sound is not None:
                        if sound.get("tempo"):
                            bpm = number(sound.get("tempo"))
                            if not 20 <= bpm <= 400:
                                warn("A tempo outside 20–400 BPM was ignored.")
                            else:
                                if when in tempos and tempos[when] != bpm:
                                    warn("Conflicting part tempos were found; the last marking is used.")
                                tempos[when] = bpm
                        if sound.get("dynamics"):
                            velocity = max(0.1, min(1.0, number(sound.get("dynamics")) / 100))
                        if any(sound.get(key) for key in ("dacapo", "dalsegno", "tocoda", "fine")):
                            warn("D.C., D.S., coda and fine navigation is not supported; playback follows written order and simple repeats.")
                    dynamic = item.find("direction-type/dynamics")
                    if dynamic is not None and len(dynamic):
                        velocity = DYNAMICS.get(dynamic[0].tag, velocity)
                    for tag, message in (
                        ("wedge", "Crescendo and diminuendo hairpins are not performed; marked dynamics still apply."),
                        ("pedal", "Piano pedal markings are not performed."),
                        ("octave-shift", "Ottava (8va/8vb) lines are not performed; check the sounding octave."),
                        ("segno", "Segno and coda navigation is not performed."),
                        ("coda", "Segno and coda navigation is not performed."),
                    ):
                        if item.find(f".//{tag}") is not None:
                            warn(message)
                    if sound is None or not sound.get("tempo"):
                        metronome = item.find("direction-type/metronome")
                        if metronome is not None:
                            unit = {"whole": 4, "half": 2, "quarter": 1, "eighth": 0.5}.get(metronome.findtext("beat-unit"))
                            per_minute = metronome.findtext("per-minute")
                            if unit and per_minute:
                                dots = len(metronome.findall("beat-unit-dot"))
                                bpm = number(per_minute) * unit * (2 - 0.5 ** dots)
                                if 20 <= bpm <= 400:
                                    tempos[when] = bpm
                            else:
                                warn("An unsupported metronome marking was ignored.")
                    if item.find("direction-type/words") is not None:
                        warn("Text directions (such as ritardando or playing instructions) are not interpreted unless exported as tempo or dynamic data.")
                elif item.tag == "note":
                    if item.find("grace") is not None:
                        warn("Grace notes are not performed.")
                        continue
                    duration = number(item.findtext("duration")) / divisions
                    if duration <= 0:
                        warn("A note with missing or invalid duration was skipped.")
                        continue
                    chord = item.find("chord") is not None
                    start = previous_start if chord else cursor
                    if not chord:
                        previous_start = start
                        cursor += duration
                    furthest = max(furthest, start + duration, cursor)
                    if item.find("rest") is not None:
                        continue
                    if item.find("cue") is not None:
                        warn("Cue notes are not performed.")
                        continue
                    pitch = item.find("pitch")
                    if pitch is None:
                        warn("Unpitched / percussion notes are not performed.")
                        continue
                    step = pitch.findtext("step")
                    if step not in STEPS:
                        warn("An unreadable pitch was skipped.")
                        continue
                    alter = number(pitch.findtext("alter"))
                    if alter != int(alter):
                        warn("Microtonal accidentals are not supported; those notes were skipped.")
                        continue
                    midi = (int(pitch.findtext("octave", "4")) + 1) * 12 + STEPS[step] + int(alter) + transpose
                    if not 21 <= midi <= 108:
                        warn("Notes outside the supported A0–C8 range were skipped.")
                        continue
                    if item.find("accidental") is not None and item.findtext("accidental") not in (
                        "sharp", "flat", "natural", "double-sharp", "sharp-sharp", "flat-flat",
                        "natural-sharp", "natural-flat", "double-flat",
                    ):
                        warn("An unsupported accidental was found; verify its pitch in the note list.")
                    notation = item.find("notations")
                    gate, gain = 0.9, velocity
                    if notation is not None:
                        if notation.find("articulations/staccato") is not None:
                            gate = 0.48
                        if notation.find("articulations/staccatissimo") is not None:
                            gate = 0.3
                        if notation.find("articulations/tenuto") is not None:
                            gate = 0.98
                        if notation.find("articulations/accent") is not None:
                            gain = min(1.0, gain * 1.2)
                        if notation.find("ornaments") is not None:
                            warn("Ornaments (including trills, turns and tremolo) are not performed.")
                        if notation.find("fermata") is not None:
                            warn("Fermatas are not lengthened; written durations are used.")
                        if notation.find("slur") is not None:
                            warn("Slur markings do not change synthesized phrasing.")
                        if notation.find("articulations/breath-mark") is not None:
                            warn("Breath marks do not add pauses.")
                        for mark in notation:
                            if mark.tag not in ("tied", "slur", "articulations", "ornaments", "fermata", "technical"):
                                warn(f"Unsupported notation: {mark.tag}. It is not performed.")
                        if notation.find("technical") is not None:
                            warn("Playing techniques (such as pizzicato, harmonics or fingerings) do not change the synthesized sound.")
                        articulations = notation.find("articulations")
                        if articulations is not None and any(a.tag not in ("staccato", "staccatissimo", "tenuto", "accent", "breath-mark") for a in articulations):
                            warn("Some articulation markings are not performed.")
                    tie_types = {t.get("type") for t in item.findall("tie") + item.findall("notations/tied")}
                    tie_key = (item.findtext("voice", "1"), midi, item.findtext("staff", "1"))
                    prior = ties.get(tie_key)
                    absolute = base + start
                    if "stop" in tie_types and prior and abs(prior["beat"] + prior["beats"] - absolute) < 0.001:
                        combined = prior["beats"] + duration
                        prior["gate"] = (prior["beats"] + duration * gate) / combined
                        prior["beats"] += duration
                        event = prior
                    else:
                        if "stop" in tie_types:
                            warn("An unmatched tie was found and played as a separate note.")
                        accidental = {0: "", 1: "♯", -1: "♭", 2: "𝄪", -2: "𝄫"}.get(int(alter), f"({int(alter):+})")
                        event = {"midi": midi, "name": f"{step}{accidental}{pitch.findtext('octave', '4')}",
                                 "beat": absolute, "beats": duration, "velocity": gain,
                                 "gate": gate, "part": part_id, "measure": measure.get("number", str(measure_index + 1))}
                        events.append(event)
                    if "start" in tie_types:
                        ties[tie_key] = event
                        event["gate"] = 1.0
                    else:
                        ties.pop(tie_key, None)
            if furthest > meter + 0.05:
                warn("Some recognized measures exceed their time signature; check rhythm and voices.")
            # Pickups and intentionally short final bars must not acquire an extra rest.
            span = furthest
            if measure.get("implicit") != "yes" and measure_index not in (0, len(measures) - 1):
                if furthest + 0.05 < meter:
                    warn("Some measures are incomplete; missing time is played as silence.")
                span = max(meter, furthest)
            base += span
            if base > 100_000 or len(events) > 20_000:
                raise ScoreError("This score is too long. Photograph a shorter passage.")
        if ties:
            warn("Some ties have no matching end; check the recognized durations.")
        total_beats = max(total_beats, base)
    if not events:
        raise ScoreError("No playable pitched notes were found. Use a straight, well-lit photo of printed sheet music.")
    if not tempos or min(tempos) > 0:
        tempos[0.0] = 100.0
        warn("No starting tempo was recognized. Playback starts at 100 BPM; adjust the speed if needed.")
    points = sorted(tempos)
    elapsed = [0.0]
    for i in range(1, len(points)):
        elapsed.append(elapsed[-1] + (points[i] - points[i - 1]) * 60 / tempos[points[i - 1]])

    def seconds(beat):
        i = max(0, bisect.bisect_right(points, beat) - 1)
        return elapsed[i] + (beat - points[i]) * 60 / tempos[points[i]]

    for event in events:
        event["time"] = round(seconds(event["beat"]), 5)
        event["duration"] = round(seconds(event["beat"] + event["beats"]) - seconds(event["beat"]), 5)
        event["rhythm"] = str(Fraction(event.pop("beats")).limit_denominator(128))
        event.pop("beat")
    duration = seconds(total_beats)
    if duration > 1200:
        raise ScoreError("Playback is limited to 20 minutes. Photograph a shorter passage.")
    events.sort(key=lambda e: (e["time"], e["midi"]))
    return {
        "title": root.findtext("work/work-title") or root.findtext("movement-title") or "Your sheet music",
        "events": events, "duration": round(duration, 5), "tempo": tempos[points[0]],
        "parts": parts, "measures": measure_count, "timeSignatures": signatures,
        "warnings": warnings, "engine": "Audiveris 5.11", "requiresReview": True,
    }
