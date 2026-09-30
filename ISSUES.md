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

## Known gaps
- **Reloading the extension** can lose up to ten seconds of watched time: the
  old copy's last report cannot reach the sidecar once Chrome has cut it off.
  Slides are sent the moment they are captured, so they are not affected.
- **OpenRouter on a free-tier key** rarely has credit for a whole note; it is
  only a real fallback with credit on the account.
- **The Claude engine** needs a token from `claude setup-token` in keys.env
  (`CLAUDE_CODE_OAUTH_TOKEN=...`); without one it is skipped. The token lasts
  about a year.

## Quality
- **Whisper base.en mishears technical words** ("weights must sound to one").
  The composer corrects most of it from the slides and context. `small.en`
  (`./scripts/setup.sh --whisper-model`) is noticeably better.
- **Very long lectures** send at most 24 slides, spread evenly, and trim the
  middle of a transcript over ~120k characters.
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
