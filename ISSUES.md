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
- Generated code that needs live market data depends on yfinance answering; it
  is told to fall back to synthetic data, and the badge in the Code tab says
  whether the section ran.

## Platform
- Chrome and Chromium only (Edge, Brave, Arc should work unmodified; untested).
- The end-to-end harness uses Chrome for Testing 131: newer builds on this Mac
  hang on headless screenshots, even of a blank page. YouTube also stops a
  headless browser after about a minute of playback, which is enough for the
  test but not a full lecture.
