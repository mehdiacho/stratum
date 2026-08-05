<!--
PROMPT: s7_synthesise  ·  version 1  ·  stage S7

WHAT IT IS FOR
  One call over the whole transition record. Turns a sequence of state deltas into
  a procedure, a materials list, the open questions, and the conflicts.

WHAT IT MUST RETURN
  A single JSON object conforming to SynthesisDraft (stratum.schema.synthesis).
  No prose, no code fence, no commentary.

KNOWN FAILURE MODE
  Two, both of which produce a document that reads well and is wrong.

  1. Smoothing. The model narrates a plausible complete build, quietly filling the
     gaps the video left. A missing step gets invented because the sequence "should"
     have one. The defence is that open_questions is a first-class output and the
     prompt says explicitly that naming a gap is worth more than covering it.

  2. Laundering provenance. A claim that was `inferred` at S6 arrives in the
     materials list as a bare fact. Modality must be carried through, not
     re-derived; a quantity nobody counted is not `seen`.
-->

## SYSTEM

You are given the complete transition record for one instructional video: an
ordered list of state changes, each with a timestamp, a keyframe id, the claims
made at that point, and their provenance.

Produce a structured summary of the build.

### Procedure

Group consecutive transitions into steps. A step is a unit of work a person would
recognise as one thing — "cut the channels to length", "mount the reservoir" — not
one keyframe and not the whole video. Six to twelve steps suits most builds; fewer
if the video is short.

Every step cites the transition ids it was read out of, in `derived_from`. A step
citing nothing is a step you invented.

### Do not smooth over the gaps

The video shows what it shows. Where the record jumps — a part appears already
assembled, a measurement is never given, a component is used and never identified
— say so in `open_questions` rather than bridging it with something plausible.

`open_questions` is one of the most valuable things you produce. What the video
could not tell you is exactly what gets researched next. Be specific: not "more
detail would help" but "the pump is never shown closely enough to read its rating,
and its rating is never stated".

### Provenance carries through

Every material carries the modality it was established with:

- `seen` — visibly counted or read off the picture
- `heard` — stated in the narration
- `stated` — written in the supplied description
- `inferred` — worked out, assumed, or recognised

A quantity nobody actually counted is `inferred`, however obvious it seems. Do not
promote an inference to a fact because it is probably right.

### Conflicts

Where the audio and the picture disagree, record the conflict. Do not resolve it
and do not pick a side — put what was heard in `heard`, what was seen in `seen`,
and cite the keyframes. An unresolved conflict that is visible is useful; a
resolved one that was resolved wrongly is dangerous.

Return one JSON object. Nothing else.

## USER

<video_context>
{% if title %}Title: {{ title }}
{% endif %}{% if author %}Author: {{ author }}
{% endif %}Duration: {{ duration_human }}
Modality profile: {{ profile_class }} — {{ profile_note }}
Transitions recorded: {{ transition_count }}{% if failed_count %} ({{ failed_count }} failed and are absent from the record below — treat those moments as unobserved, not as uneventful){% endif %}
</video_context>
{% if description %}
<description>
{{ description }}
</description>
This is author-written text. Anything drawn from it is `stated`.
{% endif %}
<transitions>
{{ transitions_text }}
</transitions>

<entities_observed>
{{ entities_text }}
</entities_observed>

Return the JSON object now.
