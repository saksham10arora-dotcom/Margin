# Known limits (v2)

Written down so they are not rediscovered the hard way.

## Not yet verified on a real machine
- **Coursera** has never been run on a real course.
- **Screen-capture fallback** (for DRM video) is implemented and reached in
  tests, but no DRM lecture has been run through it end to end.

## Verified on a real course (Udemy, LLM Engineering, 2026-09-29)
- Capture from a playing Udemy lecture at 1.75x: 7 slides, Udemy auto captions,
  watched ranges, a note with the lecture's own slides embedded.
- Gemini 3.5 Flash overloaded for minutes: the note came from the next model.
- The curriculum (218 items) arrives in one page; resources are read from it.

## Verified on a real course (DeepLearning.AI, ChatGPT Prompt Engineering, 2026-10-01)
- Lesson 1, opened at `#t=40` from a link: Margin jumped there (the player
  ignores `#t=` itself), took the English captions from the lesson's caption
  file, captured the slide from the video, and wrote the note, crux and quiz
  into the course's folder.
- Signed out, only a course's first lesson plays, so moving between lessons and
  notes for lessons you finished (progress 100) are covered by tests, not yet by
  a signed-in run.

## Adding to a note (2.10.5)
- "Add what's new" and the automatic updates write only the parts watched since
  the note was written (and slides kept since), with the note so far as context,
  and merge them in: new topics at their time, more on a topic added after what
  its section says, new practice items appended, nothing else touched. A note
  from before 2.10.5 gets one guarded full write first (kept if it comes out
  thinner), then updates.
- 2.10.7: a section is only ever added to. 2.10.5 took a model's longer
  rewrite of a section in place of it, and a real one dropped three of the
  Master Theorem's slides. Updates now write only what is new for a topic, and
  anything a model still leaves out of a section is put back where it was.

## Known gaps
- **The crux** is written with a lecture's first note. Updates add sections and
  practice items but leave the crux as it was; **Rewrite the notes** or the
  Crux tab's button writes it again from everything.
- **Animated slides** (a number grid, a typing animation) keep a capture for
  each state they were caught in.
- **Reloading the extension** can lose up to ten seconds of watched time: the
  old copy's last report cannot reach the sidecar once Chrome has cut it off.
  Slides are sent the moment they are captured, so they are not affected.
- **OpenRouter on a free-tier key** rarely has credit for a whole note; it is
  only a real fallback with credit on the account.
- **The Claude engine** needs a token from `claude setup-token` in keys.env
  (`CLAUDE_CODE_OAUTH_TOKEN=...`); without one it is skipped. The token lasts
  about a year.

## Quality
- **Lectures written by hand on paper** (a hand and pen filmed from above) keep
  one picture per distinct state of the page: the hand, its pen and its shadow
  are left out of comparisons, a page nudged while writing is lined up again,
  and a capture that shows writing no other capture shows is always kept. A 10
  minute handwritten lecture kept 47 captures before 2.10.1 and keeps about 15
  now; a page taller than the frame keeps a view of each part of it.
- **A teacher in front of a projected slide** (a smart board, a see-through
  board with the teacher's ghost behind the text): someone standing in front is
  left out of comparisons, and slides are compared by their strokes (text,
  lines, writing), so the person, the ghost and shading count neither way. A
  classroom lecture kept about half as many captures (123 to 59 live, 40 when
  folded again). Still kept separately: a slide whose annotations were wiped
  (writing disappeared), and animated content that changes between captures.
- **Whisper base.en mishears technical words** ("weights must sound to one").
  The composer corrects most of it from the slides and context. `small.en`
  (`./scripts/setup.sh --whisper-model`) is noticeably better.
- **Very long lectures** (a nine-hour one-shot) send at most 24 slides, spread
  evenly. Their transcript is over what a note is written from (~120k
  characters, about two hours of speech): the parts you watched go in whole and
  the rest is thinned evenly, whole two-minute stretches from across the
  lecture. Watching a new part marks the note out of date, so it grows as you
  watch. (Before 2.10.3 the first and last hour were kept and the rest dropped.)
- **Ask the lecture** answers from the transcript and the note; for a lecture
  with no transcript it has only the note. Each question, like each set of
  flashcards, is one request to the first model in your order.
- **Notes written before 2.9** have no crux until you press **Make the crux** in
  the Crux tab. It is written into the note unless you have edited the note;
  then it lives in the panel only.
- Generated code that needs live market data depends on yfinance answering; it
  is told to fall back to synthetic data, and the badge in the Code tab says
  whether the section ran.

## Platform
- macOS and Linux. Windows is not supported. CI runs the sidecar's tests on
  both, with Python 3.11 (the minimum) and 3.13; the installer's start-at-login
  is macOS only (on Linux, `scripts/start.sh`).
- Chrome and Chromium only (Edge, Brave, Arc should work unmodified; untested).
- The end-to-end harness uses Chrome for Testing 131: newer builds on this Mac
  hang on headless screenshots, even of a blank page. YouTube also stops a
  headless browser after about a minute of playback, which is enough for the
  test but not a full lecture.
