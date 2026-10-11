# Video smoke fixture

`video-smoke.mp4` is a generated, silent two-second test pattern (128×128,
10 fps, H.264 Baseline / YUV420P). The macOS build checks that both source
and packaged Qt can decode a real video frame, without network access.

Regenerate with:

```console
ffmpeg -f lavfi -i testsrc=size=128x128:rate=10 -t 2 -c:v libx264 -profile:v baseline -level:v 3.0 -pix_fmt yuv420p -movflags +faststart -an -y tests/fixtures/video-smoke.mp4
```
