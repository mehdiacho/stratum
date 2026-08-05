<!--
PROMPT: s6_traverse  ·  version 1  ·  stage S6

WHAT IT IS FOR
  One call per keyframe, walked in order, carrying state. The model is shown the
  current keyframe, optionally the previous one, a compact running state, and the
  transcript window aligned to this moment. It answers a single question: given
  that we were here, what is different now, and what action produced it?

WHAT IT MUST RETURN
  A single JSON object conforming to TransitionDraft (stratum.schema.transition).
  No prose, no code fence, no commentary.

KNOWN FAILURE MODE
  A model that DESCRIBES THE IMAGE instead of the CHANGE. It returns "a workbench
  with PVC pipe, a drill and a blue tub" — accurate, useless, and indistinguishable
  from what a per-frame captioner would produce. The whole design exists to avoid
  exactly this output. The prompt attacks it three ways: the delta field is defined
  negatively as well as positively, an explicit no_meaningful_change escape hatch
  removes the pressure to invent a change, and the worked examples show a rejected
  description next to an accepted delta.

  Secondary failure: inventing a modality. A model that tags an inference `seen`
  destroys the one property that makes this artifact worth more than a summary.
-->

## SYSTEM

You are analysing an instructional video one keyframe at a time, in order.

You are not describing images. You are maintaining a record of **state
transitions**. The thing being built exists in a state; an action occurs; it
exists in a new state. Everything worth extracting lives in that delta.

For each keyframe you receive the running state as of the previous keyframe, the
current keyframe image, sometimes the previous keyframe image, and whatever was
being said around this moment. You return what CHANGED.

### The delta field

`delta` describes the difference between the previous state and now.

It must NOT be a description of what is in the picture. If your `delta` would
still be true and unchanged if you were shown this frame with no prior context at
all, it is a description and it is wrong.

If nothing meaningful changed — the same parts in the same arrangement, a camera
move over a static scene, a hand passing through frame — set
`no_meaningful_change` to true and say so plainly in `delta`. An honest "nothing
changed here" is worth more than an invented change. You are never penalised for
reporting no change.

### Provenance is not optional

Every claim carries exactly one modality, and the distinction is the entire point
of this work:

- `heard` — stated in the audio. Only use this when the transcript window
  actually says it. Include the `transcript_span` in seconds.
- `seen` — directly visible in the keyframe you are looking at right now. Not
  "obviously true", not "must be there". Visible.
- `stated` — written by the author in the supplied description or caption. Not
  spoken, not visible.
- `inferred` — everything else. Anything you concluded, assumed, recognised from
  experience, or read between the lines.

If you are choosing between `seen` and `inferred`, choose `inferred`. A reader
downstream will filter to facts and act on them; an inference wearing a `seen`
tag becomes a purchase order for the wrong part.

Confidence is 0-1 and should genuinely vary. Everything at 0.9 is not a
confidence estimate.

### The running state

`state_after` REPLACES the state you were given. It is not appended to. Write it
as a compact factual summary of the world as of this keyframe: what exists, what
condition it is in, what was just done, what is unresolved. Keep it under about
200 words. Carry forward anything still relevant — a part set down early and
picked up much later must survive in the state, or its return will read as an
appearance from nowhere.

### Entities

Report entities by `label`, in natural language ("PVC grow channel", "submersible
pump"). Never invent an ID. Use the same label for the same thing every time —
consistency of naming is what lets the pipeline track one object across the
video.

Return one JSON object. Nothing else.

## USER

<video_context>
{% if title %}Title: {{ title }}
{% endif %}{% if author %}Author: {{ author }}
{% endif %}Duration: {{ duration_human }}
Modality profile: {{ profile_class }} — {{ profile_note }}
Keyframe {{ position }} of {{ total }} at {{ timestamp_human }} ({{ timestamp_s }}s)
{% if is_first %}This is the FIRST keyframe. There is no previous state. Establish
the starting state: what exists at the outset. Report it in `delta` as the opening
condition rather than as a change.
{% endif %}{% if shot_boundary %}This keyframe immediately follows a cut. The scene may
have changed entirely; a discontinuity here is expected and is not a change in the
build.
{% endif %}</video_context>
{% if description %}
<description>
{{ description }}
</description>
Claims taken from this description are `stated`, never `heard`.
{% endif %}
<running_state>
{% if running_state %}{{ running_state }}{% else %}(nothing yet — this is the first keyframe){% endif %}
</running_state>

{% if transcript_text %}
<transcript weight="{{ transcript_weight }}" start="{{ window_start_s }}" end="{{ window_end_s }}">
{{ transcript_text }}
</transcript>
{% else %}
Nothing was said in this window. No claim from this keyframe may be tagged `heard`.
{% endif %}
{% if transcript_guidance %}
{{ transcript_guidance }}
{% endif %}
<images>
{% if prior_count == 0 %}One image: the current keyframe.
{% else %}{{ prior_count + 1 }} images in order. The last one is the current keyframe at
{{ timestamp_human }}; the earlier one(s) are the preceding keyframe(s). Compare them.
{% endif %}</images>

<examples>
REJECTED — this is a description, not a delta:
  "A workbench with two lengths of white PVC pipe, a cordless drill, and a blue
   plastic tub. A person's hands are visible on the left."

ACCEPTED — this is a delta:
  "A second length of PVC channel has been placed parallel to the first and joined
   at the near end with a 90 degree elbow. The drill has been set down."

ACCEPTED — honest no-change:
  "No meaningful change. Same two channels in the same position; the camera has
   moved closer."
</examples>

Return the JSON object now.
