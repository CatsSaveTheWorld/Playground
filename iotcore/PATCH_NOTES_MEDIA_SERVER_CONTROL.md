# Media Server Control - first stage

This patch adds the first direct projector-video control path without introducing
MediaAsset/MediaMood database metadata yet.

## Django / frontend

- Existing PC card shown as `미디어 서버` now has a `영상 제어` entry.
- New media-server detail page:
  - live video list from the Pi media directory
  - title search
  - individual video playback
  - stop button
  - mood buttons as an explicit unimplemented API stub
- Mood requests return HTTP 501 JSON with `아직 구현되지 않은 기능입니다.`.
- No database migration is required.

## MQTT contract

The existing correlated Pi command channel is reused:

- `media.list_videos`
- `media.play_video` with `{ "video_id": "relative/path.mp4" }`
- `media.stop`

The Django `RemoteTaskClient` keeps its old `(success, message)` API and adds
`execute_result()` for structured results such as the video list.

## Pi agent

The existing single Pi agent is extended rather than starting a second subscriber
on the same command topic. It scans
`/home/leedowon/qleto_2tb/wallpaper/videos` and launches mpv with the verified
Wayland environment and playback flags.

Only files resolving inside the configured media root and matching supported video
extensions can be played. Arbitrary shell commands are never accepted from the
web request.
