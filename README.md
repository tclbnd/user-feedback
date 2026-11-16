# MP3 VBR seek planner

Run: `python3 mp3.py input.json output.json`

Input `{"frames":[...], "tag_bytes":N, "target":S}` — frames in file
order, each `{"len","samples","main_begin","valid"}`: `len` byte
size, `samples` decoded samples (may vary), `main_begin` reservoir
bytes the frame needs from EARLIER frames' data, `valid` false =
corrupt (skipped, produces no samples, does NOT supply reservoir).
`tag_bytes` = leading tag size; frame byte offset = tag_bytes +
sum of prior frame lens (including invalid ones — they occupy bytes).

Target = desired output sample index (0-based over VALID samples).
Plan: choose the EARLIEST frame `p` such that decoding forward from p
reaches the target AND every frame between p and the target has its
reservoir covered — i.e. for each frame j in [p..target_frame], the
bytes of valid frames in [p..j) >= main_begin of j. Walk back only
as far as needed; if covering requires passing a corrupt frame the
reservoir chain is broken → plan starts AFTER it and `reliable:false`.
Output `{"start_frame":i,"byte_offset":B,"discard":D,"reliable":bool,
"notes":[...]}`: discard = valid samples inside start_frame to drop so
decoded output begins at target (target may sit inside a frame;
discard counts only that frame's own samples). `notes` strings:
`"reservoir-ok"` | `"reservoir-broken"` | `"target-eof"` (target past
last valid sample → plan for the final frame with discard=remaining).
